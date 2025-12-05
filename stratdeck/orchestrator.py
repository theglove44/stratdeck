from __future__ import annotations

import csv
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .agents.trade_planner import TradeIdea
from .agents.trader import TraderAgent  # adjust import if your TraderAgent lives elsewhere
from .filters.human_rules import StrategyRuleSnapshot, snapshot_for_strategy
from .tools.orders import enter_paper_trade
from .tools.positions import POS_PATH, PaperPosition, PositionsStore
from .vetting import IdeaVetting, VetVerdict


# ---------- Data structures ----------


@dataclass
class OpenedPositionSummary:
    idea: TradeIdea
    vetting: IdeaVetting
    position: PaperPosition
    opened_at: datetime


@dataclass
class OpenCycleResult:
    universe: str
    strategy: str
    generated_count: int
    eligible_count: int
    opened: List[OpenedPositionSummary]


@dataclass
class OrchestratorConfig:
    """
    Configuration for a single orchestrator run.

    NOTE: max_bp_fraction is currently a placeholder until true account/BP
    integration is wired in. POP / credit-per-width filters are only applied
    if those metrics can be extracted from spread_plan.
    """
    max_trades_per_day: int = 1
    max_bp_fraction: float = 0.30
    min_pop: float = 0.50
    min_credit_per_width: float = 0.30
    allow_indexes: bool = True
    allow_equities: bool = True
    default_qty: int = 1
    idea_json_path: Path = field(
        default_factory=lambda: Path(".stratdeck/last_trade_ideas.json")
    )
    journal_path: Path = field(
        default_factory=lambda: Path(".stratdeck/auto_journal.csv")
    )
    live: bool = False
    dry_run: bool = False  # if True, never calls enter_from_idea


@dataclass
class VettedCandidate:
    index: int
    idea: Any
    allowed: bool
    violations: List[Any]
    spread_plan: Dict[str, Any]
    order_summary: Dict[str, Any]
    score: Optional[float] = None
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrchestratorResult:
    status: str  # "executed" | "skipped" | "error"
    reason: str
    picked_index: Optional[int] = None
    picked_candidate: Optional[VettedCandidate] = None
    execution_result: Any = None
    candidates: List[VettedCandidate] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def is_eligible(vetting: IdeaVetting, min_score: float) -> bool:
    return vetting.verdict is VetVerdict.ACCEPT and vetting.score >= min_score


def select_trades(
    vetted: Sequence[Tuple[TradeIdea, IdeaVetting]],
    max_trades: int,
    min_score: float,
) -> List[Tuple[TradeIdea, IdeaVetting]]:
    eligible = [
        (idea, vet)
        for idea, vet in vetted
        if is_eligible(vet, min_score)
    ]
    eligible.sort(key=lambda pair: pair[1].score, reverse=True)
    return list(eligible[:max_trades])


def _open_paper_position_from_idea(idea: TradeIdea, qty: int) -> PaperPosition:
    """
    Default adapter: reuse the existing paper path used by enter-auto CLI.

    1. Call enter_paper_trade(idea, qty).
    2. Read the resulting PaperPosition from the positions JSON ledger.
    """
    result = enter_paper_trade(idea, qty=qty)
    pos_id = result.get("position_id")
    if not pos_id:
        raise RuntimeError("enter_paper_trade returned no position_id")

    store = PositionsStore(POS_PATH)
    position = store.get(pos_id)
    if position is None:
        raise RuntimeError(f"PositionsStore missing position_id={pos_id}")

    return position


def run_open_cycle(
    universe: str,
    strategy: str,
    max_trades: int,
    min_score: float,
    *,
    qty: int = 1,
    idea_generator: Callable[[str, str], List[TradeIdea]] | None = None,
    vet_one: Callable[[TradeIdea, StrategyRuleSnapshot], IdeaVetting] | None = None,
    open_from_idea: Callable[[TradeIdea, int], PaperPosition] | None = None,
) -> OpenCycleResult:
    """
    Pure orchestrator for the daily open cycle (paper-only).

    Steps:
    1. Generate TradeIdeas for (universe, strategy).
    2. Vet each idea using the existing vetting core and rules snapshot.
    3. Filter to ideas with verdict=ACCEPT and score >= min_score.
    4. Sort by score descending.
    5. Select up to max_trades.
    6. Open each selected idea in the paper trading engine.
    7. Return an OpenCycleResult summary.
    """
    if idea_generator is None:
        from stratdeck.agents.trade_planner import generate_trade_ideas

        idea_generator = generate_trade_ideas

    if vet_one is None:
        from stratdeck.vetting import vet_single_idea

        vet_one = vet_single_idea

    if open_from_idea is None:
        open_from_idea = _open_paper_position_from_idea

    from stratdeck.strategies import load_strategy_config

    strategy_cfg = load_strategy_config()
    rules = snapshot_for_strategy(strategy, cfg=strategy_cfg)

    ideas = idea_generator(universe, strategy)

    vetted_pairs: List[Tuple[TradeIdea, IdeaVetting]] = []
    for idea in ideas:
        vet_result = vet_one(idea, rules)
        vetted_pairs.append((idea, vet_result))

    eligible_pairs = [
        (idea, vet) for idea, vet in vetted_pairs if is_eligible(vet, min_score)
    ]
    selected_pairs = select_trades(
        vetted_pairs,
        max_trades=max_trades,
        min_score=min_score,
    )

    opened: List[OpenedPositionSummary] = []
    now = datetime.utcnow()

    for idea, vet in selected_pairs:
        pos = open_from_idea(idea, qty)
        opened.append(
            OpenedPositionSummary(
                idea=idea,
                vetting=vet,
                position=pos,
                opened_at=now,
            )
        )

    return OpenCycleResult(
        universe=universe,
        strategy=strategy,
        generated_count=len(ideas),
        eligible_count=len(eligible_pairs),
        opened=opened,
    )


# ---------- Orchestrator implementation ----------


class Orchestrator:
    """
    Glue layer on top of existing StratDeck agents.

    Responsibilities:
    - Regenerate ideas via the existing trade-ideas CLI flow.
    - Vet ideas via TraderAgent.vet_idea.
    - Filter, rank, and choose the best candidate(s).
    - Enter a paper (or live) trade via TraderAgent.enter_from_idea.
    - Journal executed trades to a CSV for audit / later analysis.
    """

    def __init__(
        self,
        trader: TraderAgent,
        config: OrchestratorConfig,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.trader = trader
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

    # ---- Public API ----

    def run_once(self) -> OrchestratorResult:
        """
        Run a single orchestration cycle.

        Steps:
        - Enforce max_trades_per_day using the journal CSV.
        - Generate fresh ideas via the trade-ideas CLI.
        - Vet all ideas via TraderAgent.vet_idea.
        - Apply POP / credit-per-width / index-vs-equity filters.
        - Rank and select the best candidate.
        - If not dry_run:
            - Call TraderAgent.enter_from_idea (paper by default).
            - Journal the execution.
        - Return an OrchestratorResult with a JSON-friendly payload.
        """
        try:
            trades_today = self._count_trades_today()
            if self.config.max_trades_per_day > 0 and trades_today >= self.config.max_trades_per_day:
                reason = f"max_trades_per_day_reached({trades_today}/{self.config.max_trades_per_day})"
                self.logger.info("Skipping auto run: %s", reason)
                return OrchestratorResult(
                    status="skipped",
                    reason=reason,
                    candidates=[],
                )

            ideas = self._generate_ideas()
            if not ideas:
                reason = "no_ideas_generated"
                self.logger.info("No ideas generated, skipping.")
                return OrchestratorResult(
                    status="skipped",
                    reason=reason,
                    candidates=[],
                )

            candidates = self._vet_candidates(ideas)

            # Apply filters and compute scores
            filtered: List[VettedCandidate] = []
            for c in candidates:
                if not c.allowed:
                    continue
                if not self._passes_filters(c):
                    continue
                c.score = self._score_candidate(c)
                filtered.append(c)

            if not filtered:
                reason = "no_candidates_passed_filters"
                self.logger.info("No candidates passed filters.")
                return OrchestratorResult(
                    status="skipped",
                    reason=reason,
                    candidates=candidates,
                )

            # For now: we only ever enter at most one trade per run
            best = max(filtered, key=lambda c: c.score or 0.0)
            self.logger.info(
                "Selected candidate index=%s score=%.4f",
                best.index,
                best.score or 0.0,
            )

            execution_result: Any = None
            if self.config.dry_run:
                self.logger.info("Dry-run: not calling enter_from_idea.")
                reason = "dry_run"
                status = "skipped"
            else:
                execution_result = self._execute_candidate(best)
                self._journal_execution(best, execution_result)
                reason = "executed"
                status = "executed"

            return OrchestratorResult(
                status=status,
                reason=reason,
                picked_index=best.index,
                picked_candidate=best,
                execution_result=execution_result,
                candidates=candidates,
            )

        except Exception as exc:  # noqa: BLE001 – we want a hard catch here
            self.logger.exception("Orchestrator.run_once failed: %s", exc)
            return OrchestratorResult(
                status="error",
                reason=str(exc),
                candidates=[],
            )

    # ---- Internal helpers ----

    def _count_trades_today(self) -> int:
        """
        Count how many auto trades have been journaled for today's date.
        If the journal file doesn't exist yet, returns 0.
        """
        path = self.config.journal_path
        if not path.exists():
            return 0

        today_str = date.today().isoformat()
        count = 0

        try:
            with path.open("r", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("date") == today_str and row.get("status") == "executed":
                        count += 1
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Failed to read journal for trade count: %s", exc)

        return count

    def _generate_ideas(self) -> List[Any]:
        """
        Call the existing trade-ideas CLI to regenerate the ideas JSON file,
        then load and return the ideas.

        This preserves the semantics of the current CLI. Later we can refactor
        to call a shared Python function instead of shelling out.
        """
        path = self.config.idea_json_path
        path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            "-m",
            "stratdeck.cli",
            "trade-ideas",
            "--json-output",
            str(path),
        ]
        self.logger.info("Running trade-ideas CLI: %s", " ".join(cmd))

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"trade-ideas CLI failed with code {proc.returncode}: {proc.stderr.strip()}"
            )

        if not path.exists():
            raise FileNotFoundError(f"Ideas JSON not found at {path}")

        with path.open("r") as f:
            data = json.load(f)

        # Support both [ideas...] and {"ideas": [...]} shapes
        if isinstance(data, list):
            ideas = data
        elif isinstance(data, dict) and "ideas" in data:
            ideas = data["ideas"]
        else:
            raise ValueError(
                f"Unexpected ideas JSON structure in {path}: {type(data)}"
            )

        self.logger.info("Loaded %d ideas from %s", len(ideas), path)
        return ideas

    def _vet_candidates(self, ideas: List[Any]) -> List[VettedCandidate]:
        """
        Run TraderAgent.vet_idea for each idea.

        Assumes TraderAgent.vet_idea signature:
            allowed, violations, spread_plan, order_summary = vet_idea(idea, qty)
        """
        candidates: List[VettedCandidate] = []
        for idx, idea in enumerate(ideas):
            try:
                allowed, violations, spread_plan, order_summary = self.trader.vet_idea(
                    idea,
                    qty=self.config.default_qty,
                )
            except TypeError:
                # If your vet_idea signature differs, adjust this call.
                allowed, violations, spread_plan, order_summary = self.trader.vet_idea(
                    idea,
                    self.config.default_qty,
                )

            # Normalise violations into a list of strings
            if isinstance(violations, list):
                violations_list = violations
            elif violations is None:
                violations_list = []
            else:
                # collapse any non-list into a single string entry, not chars
                violations_list = [str(violations)]

            # Ensure spread_plan/order_summary are dict-like for downstream use
            if not isinstance(spread_plan, dict):
                spread_plan = {}
            if not isinstance(order_summary, dict):
                order_summary = {}

            metrics = self._extract_metrics(spread_plan)

            candidate = VettedCandidate(
                index=idx,
                idea=idea,
                allowed=bool(allowed),
                violations=violations_list,
                spread_plan=spread_plan,
                order_summary=order_summary,
                metrics=metrics,
            )
            candidates.append(candidate)

        return candidates

    def _extract_metrics(self, spread_plan: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """
        Pull core metrics out of spread_plan in a tolerant way.

        You WILL need to align these keys with however your TraderAgent
        currently structures spread_plan.

        Expected keys (or equivalents you map to):
        - credit: "net_credit" or "credit"
        - width: "width", "spread_width", or "max_loss_width"
        - pop:   "pop", "probability_of_profit", or "pop_pct"
        - bp_effect: "bp_effect", "buying_power_effect", or "bp_change"
        """
        # If spread_plan is not a mapping (defensive), bail out with empty metrics
        if not isinstance(spread_plan, dict):
            return {
                "credit": None,
                "width": None,
                "pop": None,
                "credit_per_width": None,
                "bp_effect": None,
            }

        def _float_or_none(value: Any) -> Optional[float]:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        credit = None
        for key in ("net_credit", "credit"):
            if key in spread_plan:
                credit = _float_or_none(spread_plan.get(key))
                break

        width = None
        for key in ("width", "spread_width", "max_loss_width"):
            if key in spread_plan:
                width = _float_or_none(spread_plan.get(key))
                break

        pop = None
        for key in ("pop", "probability_of_profit", "pop_pct"):
            if key in spread_plan:
                pop = _float_or_none(spread_plan.get(key))
                if pop is not None and pop > 1.0:
                    pop = pop / 100.0
                break

        bp_effect = None
        for key in ("bp_effect", "buying_power_effect", "bp_change"):
            if key in spread_plan:
                bp_effect = _float_or_none(spread_plan.get(key))
                break

        credit_per_width: Optional[float] = None
        if credit is not None and width not in (None, 0.0):
            credit_per_width = credit / width

        return {
            "credit": credit,
            "width": width,
            "pop": pop,
            "credit_per_width": credit_per_width,
            "bp_effect": bp_effect,
        }

    def _passes_filters(self, candidate: VettedCandidate) -> bool:
        """
        Apply config-based filters (POP, credit/width, index vs equity).
        """
        m = candidate.metrics

        pop = m.get("pop")
        if pop is not None and pop < self.config.min_pop:
            return False

        cpw = m.get("credit_per_width")
        if cpw is not None and cpw < self.config.min_credit_per_width:
            return False

        symbol = self._extract_symbol(candidate)
        if symbol:
            is_index = self._is_index(symbol)
            if is_index and not self.config.allow_indexes:
                return False
            if (not is_index) and not self.config.allow_equities:
                return False

        # max_bp_fraction is NOT enforced here yet; we need real account BP.
        return True

    def _score_candidate(self, candidate: VettedCandidate) -> float:
        """
        Simple scoring function: weight credit/width heavily, POP second.
        Tweak freely later; this is intentionally simple.
        """
        m = candidate.metrics
        cpw = m.get("credit_per_width") or 0.0
        pop = m.get("pop") or 0.0
        credit = m.get("credit") or 0.0

        # Example: primary = cpw, secondary = pop, tiny bump for absolute credit
        score = cpw * 100.0 + pop * 10.0 + credit * 0.1
        return float(score)

    def _execute_candidate(self, candidate: VettedCandidate) -> Any:
        """
        Call TraderAgent.enter_from_idea with paper/live toggle.
        """
        idea = candidate.idea
        qty = self.config.default_qty

        # Assumes signature: enter_from_idea(idea, qty, confirm, live_order)
        # If your signature includes a portfolio or other args, adjust here.
        self.logger.info(
            "Executing candidate index=%s qty=%s live=%s",
            candidate.index,
            qty,
            self.config.live,
        )
        try:
            result = self.trader.enter_from_idea(
                idea,
                qty,
                True,               # confirm=True -> actually place (paper or live)
                self.config.live,   # live_order flag
            )
        except TypeError:
            # Fallback in case your method uses named args
            result = self.trader.enter_from_idea(
                idea=idea,
                qty=qty,
                confirm=True,
                live_order=self.config.live,
            )

        return result

    def _journal_execution(
        self,
        candidate: VettedCandidate,
        execution_result: Any,
    ) -> None:
        """
        Append a single row to the auto journal CSV.

        This is intentionally minimal; we'll extend fields later.
        """
        path = self.config.journal_path
        path.parent.mkdir(parents=True, exist_ok=True)

        metrics = candidate.metrics
        symbol = self._extract_symbol(candidate) or ""

        now = datetime.now()
        row = {
            "date": now.date().isoformat(),
            "time": now.time().isoformat(timespec="seconds"),
            "status": "executed",
            "symbol": symbol,
            "candidate_index": candidate.index,
            "score": candidate.score if candidate.score is not None else "",
            "credit": metrics.get("credit", ""),
            "width": metrics.get("width", ""),
            "credit_per_width": metrics.get("credit_per_width", ""),
            "pop": metrics.get("pop", ""),
            "bp_effect": metrics.get("bp_effect", ""),
            "qty": self.config.default_qty,
            "live": self.config.live,
        }

        fieldnames = [
            "date",
            "time",
            "status",
            "symbol",
            "candidate_index",
            "score",
            "credit",
            "width",
            "credit_per_width",
            "pop",
            "bp_effect",
            "qty",
            "live",
        ]

        write_header = not path.exists()
        try:
            with path.open("a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Failed to journal execution: %s", exc)

    # ---- Utility helpers ----

    def _extract_symbol(self, candidate: VettedCandidate) -> Optional[str]:
        """
        Try to pull a symbol/ticker from idea or spread_plan.

        Adjust keys here if your idea/spread_plan uses different field names.
        """
        idea = candidate.idea
        sp = candidate.spread_plan

        for obj in (idea, sp):
            if isinstance(obj, dict):
                for key in ("symbol", "underlying", "ticker"):
                    if key in obj and obj[key]:
                        return str(obj[key]).upper()
        return None

    @staticmethod
    def _is_index(symbol: str) -> bool:
        """
        Heuristic to decide if a symbol is an index.
        Extend/adjust as needed.
        """
        idx_symbols = {"SPX", "XSP", "NDX", "RUT"}
        return symbol.upper() in idx_symbols
