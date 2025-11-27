# stratdeck/agents/trade_planner.py

from __future__ import annotations
import logging
import os
import sys
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from ..strategy_engine import SymbolStrategyTask, choose_target_dte, choose_width
from ..tools.chain_pricing_adapter import ChainPricingAdapter
from ..tools.filters import FilterDecision, evaluate_candidate_filters
from ..tools.retries import call_with_retries
from ..tools.ta import resolve_symbols

if TYPE_CHECKING:
    from stratdeck.data.provider import IDataProvider

log = logging.getLogger(__name__)
DEBUG_FILTERS = os.getenv("STRATDECK_DEBUG_STRATEGY_FILTERS") == "1" or os.getenv(
    "STRATDECK_DEBUG_FILTERS"
) == "1"


def _extract_price_from_quote(quote: Any) -> Tuple[Optional[float], Optional[str]]:
    """
    Pull the first non-None price from a quote dict in mid→mark→last order.
    """
    if not isinstance(quote, dict):
        return None, None

    for key in ("mid", "mark", "last"):
        val = quote.get(key)
        if val is None:
            continue
        try:
            return float(val), key
        except Exception:
            continue
    return None, None


def _spx_fallback_via_xsp(
    fetcher: Optional[Any],
) -> Tuple[Optional[float], Optional[str]]:
    """
    If SPX quotes fail, try XSP and rescale (approx 1/10th the size).
    """
    if fetcher is None:
        return None, None
    try:
        quote = call_with_retries(
            lambda: fetcher("XSP"),
            label="quote XSP fallback",
            logger=log,
        )
    except Exception as exc:
        log.warning(
            "underlying_price_hint spx fallback via xsp failed error=%r", exc
        )
        return None, None

    fallback_price, fallback_source = _extract_price_from_quote(quote)
    if fallback_price is None:
        log.warning(
            "underlying_price_hint spx fallback via xsp missing price quote=%r",
            quote,
        )
        return None, None

    synthetic_spx = fallback_price * 10.0
    log.info(
        "underlying_price_hint spx fallback via xsp source=%s xsp_price=%.4f synthetic_price=%.4f",
        fallback_source,
        fallback_price,
        synthetic_spx,
    )
    return synthetic_spx, fallback_source


def resolve_underlying_price_hint(
    symbol: str,
    data_symbol: str,
    provider: Optional["IDataProvider"],
    ta_price_hint: Optional[float] = None,
    chartist: Optional[Any] = None,
) -> Optional[float]:
    """
    Resolve the underlying price hint for a trade idea using this precedence:

      1. Live quote via provider.get_quote(symbol) with bounded retries.
      2. Cached quote (if provider exposes get_cached_quote / get_quote_cached).
      3. TA/Chartist hint (provided via ta_price_hint or optional chartist helper).
      4. Final fallback returns None with a warning when no sources resolve.
    """
    sym = (symbol or data_symbol or "").strip().upper()
    data_sym = (data_symbol or "").strip()
    fetcher = getattr(provider, "get_quote", None) if provider is not None else None
    cached_fetcher = None
    if provider is not None:
        cached_fetcher = getattr(provider, "get_cached_quote", None) or getattr(
            provider, "get_quote_cached", None
        )

    # --- 1) Live quote path -------------------------------------------------
    if fetcher is not None and sym:
        try:
            quote = call_with_retries(
                lambda: fetcher(sym),
                label=f"quote {sym}",
                logger=log,
            )
        except Exception as exc:
            log.warning(
                "underlying_price_hint live quote failed symbol=%s error=%r", sym, exc
            )
            quote = None

        live_price, source = _extract_price_from_quote(quote) if quote else (None, None)
        if live_price is None and sym == "SPX":
            live_price, source = _spx_fallback_via_xsp(fetcher)

        if live_price is not None:
            log.info(
                "underlying_price_hint live quote used symbol=%s source=%s price=%.4f",
                sym,
                source,
                live_price,
            )
            return float(live_price)

        if quote is not None:
            log.warning(
                "underlying_price_hint live quote missing price symbol=%s quote=%r; falling back",
                sym,
                quote,
            )

    # --- 1b) Cached quote fallback -----------------------------------------
    if cached_fetcher is not None and sym:
        try:
            cached_quote = cached_fetcher(sym)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "underlying_price_hint cached quote failed symbol=%s error=%r",
                sym,
                exc,
            )
            cached_quote = None

        cached_price, source = (
            _extract_price_from_quote(cached_quote) if cached_quote else (None, None)
        )
        if cached_price is not None:
            log.info(
                "underlying_price_hint cached quote used symbol=%s source=%s price=%.4f",
                sym,
                source,
                cached_price,
            )
            return float(cached_price)

    # --- 2) TA / Chartist hint ---------------------------------------------
    ta_hint = ta_price_hint
    if ta_hint is None and chartist is not None:
        price_fn = getattr(chartist, "get_price_hint", None) or getattr(
            chartist, "price_hint", None
        )
        if callable(price_fn):
            try:
                ta_hint = price_fn(data_sym or sym)
            except Exception as exc:
                log.warning(
                    "underlying_price_hint chartist fallback failed symbol=%s data_symbol=%s error=%r",
                    sym,
                    data_sym,
                    exc,
                )

    if ta_hint is not None:
        try:
            return float(ta_hint)
        except Exception:
            log.warning(
                "underlying_price_hint ta fallback non-numeric symbol=%s data_symbol=%s value=%r",
                sym,
                data_sym,
                ta_hint,
            )

    # --- 3) Final fallback --------------------------------------------------
    log.warning(
        "underlying_price_hint fallback missing live+ta symbol=%s data_symbol=%s provider_present=%s",
        sym,
        data_sym,
        provider is not None,
    )
    return None


def _log_filter_decision(candidate: Dict[str, Any], decision: FilterDecision) -> None:
    if not DEBUG_FILTERS:
        return

    payload = {
        "symbol": candidate.get("symbol"),
        "strategy_type": candidate.get("strategy_type"),
        "dte_target": candidate.get("dte_target"),
        "ivr": candidate.get("ivr"),
        "pop": candidate.get("pop"),
        "credit_per_width": candidate.get("credit_per_width"),
        "accepted": decision.passed,
        "applied": decision.applied,
        "reasons": decision.reasons,
    }
    log.debug("[filters] %s", payload)


@dataclass
class TradeLeg:
    """
    A single options leg.

    side: "short" or "long"
    type: "call" or "put"
    strike: numeric strike price
    expiry: string or date repr (e.g. "2025-01-17" or "45DTE" placeholder)
    quantity: contracts per leg (positive integer; side encodes long/short)
    """

    side: str
    type: str
    strike: float
    expiry: Optional[str]
    quantity: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TradeIdea:
    """
    A structured trade idea generated from TA + scout context.

    This is intentionally broker-agnostic. Your existing trader.py module
    can map this into real option contracts and order plans.
    """

    symbol: str
    data_symbol: str
    trade_symbol: str
    strategy: str  # "iron_condor", "short_put_spread", etc.
    direction: str  # "bullish", "bearish", "neutral", etc.
    vol_context: str  # "normal", "elevated", "expansion_likely"
    rationale: str  # one-paragraph explanation
    legs: List[TradeLeg]
    underlying_price_hint: Optional[float] = None
    dte_target: Optional[int] = None
    spread_width: Optional[float] = None
    target_delta: Optional[float] = None
    notes: List[str] = None
    # NEW: chain-based metrics for TraderAgent / selection logic
    ivr: Optional[float] = None
    pop: Optional[float] = None
    credit_per_width: Optional[float] = None
    estimated_credit: Optional[float] = None  # total net credit for the structure
    # Provenance + filter metadata
    strategy_id: Optional[str] = None
    universe_id: Optional[str] = None
    filters_passed: Optional[bool] = None
    filters_applied: Optional[Dict[str, float]] = None
    filter_reasons: Optional[List[str]] = None  # reasons present even when passed

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["legs"] = [leg.to_dict() for leg in self.legs]
        return d


class TradePlanner:
    """
    TradePlanner: consumes TA-enriched scan rows and proposes options structures.

    Inputs (per scan row):
      {
        "symbol": "SPX",
        "score": 0.48,              # from ScoutAgent (optional)
        "ta": { ... TA_RESULT ...}, # from ChartistEngine
        "ta_score": 0.12,
        "ta_directional_bias": "slightly_bullish",
        "ta_vol_bias": "normal",
        "strategy_hint": "short_premium_range" | "short_premium_trend" | "long_premium_breakout" (optional)
      }

    Outputs:
      - list[TradeIdea]
    """

    def __init__(
        self,
        chains_client: Any = None,
        pricing_client: Any = None,
    ) -> None:
        """
        chains_client / pricing_client allow integration with stratdeck.tools.chains
        and stratdeck.tools.pricing.

        If chains_client is not provided, a default ChainPricingAdapter instance
        is created so that chain-based metrics (POP, credit_per_width) can be
        computed when available.
        """
        if chains_client is None:
            chains_client = ChainPricingAdapter()

        self.chains_client = chains_client
        self.pricing_client = pricing_client

    # ---------- Public API ----------

    def generate_from_scan_results(
        self,
        scan_rows: Sequence[Dict[str, Any]],
        default_strategy: str = "short_premium_range",
        dte_target: int = 45,
        max_per_symbol: int = 1,
    ) -> List[TradeIdea]:
        """
        Main entry: turn TA-enriched scan rows into a list of TradeIdea objects.
        """
        ideas: List[TradeIdea] = []

        for row in scan_rows:
            symbol = row.get("symbol")
            if not symbol:
                continue

            ta = row.get("ta") or {}
            strategy_hint = row.get("strategy_hint", default_strategy)

            per_symbol_ideas = self._generate_for_symbol(
                symbol=symbol,
                row=row,
                ta=ta,
                strategy_hint=strategy_hint,
                dte_target=dte_target,
            )

            if not per_symbol_ideas:
                continue

            ideas.extend(per_symbol_ideas[:max_per_symbol])

        return ideas

    def generate_from_scan_results_with_strategies(
        self,
        scan_rows: Sequence[Dict[str, Any]],
        tasks: Sequence[SymbolStrategyTask],
        dte_target: int = 45,
        max_per_symbol: int = 1,
    ) -> List[TradeIdea]:
        """
        Strategy-aware variant of generate_from_scan_results.

        Instead of using only a high-level strategy_hint from TA, this path
        consumes (symbol, strategy, universe) tasks produced by the
        strategy_engine and uses StrategyTemplate config (DTE rules, width
        rules, filters) to shape each TradeIdea.

        The external TradeIdea schema remains unchanged.
        """
        if not scan_rows or not tasks:
            return []

        task_map: Dict[str, List[SymbolStrategyTask]] = {}
        for task in tasks:
            key = str(task.symbol).upper()
            task_map.setdefault(key, []).append(task)

        ideas: List[TradeIdea] = []

        for row in scan_rows:
            symbol = row.get("symbol")
            if not symbol:
                continue

            symbol_key = str(symbol).upper()
            symbol_tasks = task_map.get(symbol_key)
            if not symbol_tasks:
                continue

            ta = row.get("ta") or {}
            per_symbol_ideas: List[TradeIdea] = []

            for task in symbol_tasks:
                idea = self._generate_for_task(
                    symbol=symbol_key,
                    row=row,
                    ta=ta,
                    task=task,
                    default_dte_target=dte_target,
                )
                if idea is not None:
                    per_symbol_ideas.append(idea)

            if not per_symbol_ideas:
                continue

            ideas.extend(per_symbol_ideas[:max_per_symbol])

        return ideas

    # ---------- Internals ----------

    def _get_provider_if_live(self) -> Optional["IDataProvider"]:
        mode = os.getenv("STRATDECK_DATA_MODE", "mock").lower()
        if mode != "live":
            return None

        try:
            from stratdeck.data.factory import get_provider

            return get_provider()
        except Exception as exc:  # pragma: no cover - defensive guard
            if DEBUG_FILTERS:
                log.debug(
                    "resolve_underlying_price_hint: provider unavailable mode=%s error=%r",
                    mode,
                    exc,
                )
            return None

    def _generate_for_task(
        self,
        symbol: str,
        row: Dict[str, Any],
        ta: Dict[str, Any],
        task: SymbolStrategyTask,
        default_dte_target: int,
    ) -> Optional[TradeIdea]:
        """
        Strategy-template aware idea builder for a single (symbol, strategy) task.

        Uses TA context + StrategyTemplate (dte, width_rule, filters) to produce
        a TradeIdea, while keeping the TradeIdea schema unchanged.
        """
        scores = ta.get("scores", {}) or {}
        structure = ta.get("structure") or {}
        trend_regime = (ta.get("trend_regime") or {}).get("state", "unknown")
        vol_regime = (ta.get("vol_regime") or {}).get("state", "unknown")

        dir_bias = row.get(
            "ta_directional_bias", scores.get("directional_bias", "neutral")
        )
        vol_bias = row.get("ta_vol_bias", scores.get("vol_bias", "normal"))
        ta_score = scores.get("ta_bias", 0.0)

        support_levels: List[float] = structure.get("support") or []
        resistance_levels: List[float] = structure.get("resistance") or []
        range_info: Optional[Dict[str, Any]] = structure.get("range") or None

        strategy_type = self._strategy_type_from_template(
            template=task.strategy,
            dir_bias=dir_bias,
        )
        if strategy_type == "skip":
            return None

        data_symbol, trade_symbol = resolve_symbols(symbol)

        underlying_hint = resolve_underlying_price_hint(
            symbol=trade_symbol,
            data_symbol=data_symbol,
            provider=self._get_provider_if_live(),
            ta_price_hint=self._infer_underlying_price_hint(
                support_levels=support_levels,
                resistance_levels=resistance_levels,
                range_info=range_info,
            ),
        )

        target_dte = self._select_dte_for_task(
            symbol=symbol,
            strategy=task.strategy,
            default_dte_target=default_dte_target,
        )

        width_override = self._select_width_for_task(
            strategy=task.strategy,
            underlying_hint=underlying_hint,
        )

        legs, spread_width = self._build_legs_from_ta(
            strategy_type=strategy_type,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            underlying_hint=underlying_hint,
            dte_target=target_dte,
            width_override=width_override,
        )

        if not legs:
            return None

        direction = self._direction_from_strategy(strategy_type, dir_bias)
        rationale, notes = self._build_rationale(
            symbol=symbol,
            strategy_type=strategy_type,
            dir_bias=dir_bias,
            vol_bias=vol_bias,
            trend_regime=trend_regime,
            ta_score=ta_score,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            vol_regime=vol_regime,
        )
        # --- Strategy / universe provenance ---------------------------------
        # Ensure notes is a mutable list
        notes = list(notes or [])

        # Pull stable identifiers from the StrategyTemplate / UniverseConfig
        template_name = getattr(task.strategy, "name", None) or getattr(
            task.strategy, "id", None
        )
        universe_name = getattr(task.universe, "name", None)

        provenance_parts: List[str] = []
        if template_name:
            provenance_parts.append(f"template={template_name}")
        if universe_name:
            provenance_parts.append(f"universe={universe_name}")

        if provenance_parts:
            # Machine-readable but human-friendly provenance line.
            # Example:
            #   [provenance] template=short_put_spread_index_45d universe=index_core
            notes.append("[provenance] " + " ".join(provenance_parts))
        # ---------------------------------------------------------------------

        # Canonical IVR source is row["ivr"], but we allow some fallbacks
        ivr = row.get("ivr")
        if ivr is None:
            # Optional fallbacks if your scan rows currently use other keys.
            # If you don't have these, you can drop this block or adapt it.
            ivr = row.get("iv_rank") or row.get("iv_rank_1y") or row.get("iv_rank_1yr")

        # --- Chain-based metrics (POP, credit_per_width, estimated_credit) -
        pop: Optional[float] = None
        credit_per_width: Optional[float] = None
        estimated_credit: Optional[float] = None

        if self.chains_client is not None:
            price_fn = getattr(self.chains_client, "price_structure", None)
            if price_fn is not None:
                try:
                    pricing = price_fn(
                        symbol=symbol,
                        strategy_type=strategy_type,
                        legs=legs,
                        dte_target=target_dte,
                        # For now, we use the same 0.20 target delta you hard-code
                        # into TradeIdea.target_delta. This can be wired to
                        # StrategyTemplate later if you like.
                        target_delta_hint=0.20,
                    )
                except Exception as exc:
                    pricing = None
                    if DEBUG_FILTERS:
                        log.debug(
                            "Chain pricing failed: symbol=%s strategy=%s error=%r",
                            symbol,
                            strategy_type,
                            exc,
                        )
                if pricing:
                    pop = pricing.get("pop", pop)
                    credit_per_width = pricing.get("credit_per_width", credit_per_width)
                    estimated_credit = pricing.get("credit", estimated_credit)

        candidate: Dict[str, Any] = {
            "symbol": symbol,
            "strategy_type": strategy_type,
            "direction": direction,
            "spread_width": spread_width,
            "dte_target": target_dte,
            "pop": pop,
            "ivr": ivr,
            "credit_per_width": credit_per_width,
            "estimated_credit": estimated_credit,
        }

        if DEBUG_FILTERS:
            print("[trade-ideas] candidate before filters:", candidate, file=sys.stderr)

        decision = self._evaluate_strategy_filters(candidate, task.strategy)
        _log_filter_decision(candidate, decision)

        if not decision.passed:
            return None

        idea = TradeIdea(
            symbol=symbol,
            data_symbol=data_symbol,
            trade_symbol=trade_symbol,
            strategy=strategy_type,
            direction=direction,
            vol_context=vol_bias,
            rationale=rationale,
            legs=legs,
            underlying_price_hint=underlying_hint,
            dte_target=target_dte,
            spread_width=spread_width,
            target_delta=0.20,
            notes=notes,
            ivr=ivr,
            # NEW: expose chain-based metrics to TraderAgent
            pop=pop,
            credit_per_width=credit_per_width,
            estimated_credit=estimated_credit,
            strategy_id=template_name,
            universe_id=universe_name,
            filters_passed=decision.passed,
            filters_applied=decision.applied or {},
            filter_reasons=decision.reasons or [],
        )
        return idea


    def _select_dte_for_task(
        self,
        symbol: str,
        strategy: Any,
        default_dte_target: int,
    ) -> int:
        dte_rule = getattr(strategy, "dte", None)
        if dte_rule is None:
            return default_dte_target

        available_dtes = self._get_available_dtes(symbol)
        if not available_dtes:
            return default_dte_target

        target = choose_target_dte(available_dtes, dte_rule)
        if target is None:
            return default_dte_target
        return target

    def _get_available_dtes(self, symbol: str) -> Sequence[int]:
        if self.chains_client is None:
            return []

        getter = getattr(self.chains_client, "get_available_dtes", None)
        if getter is None:
            return []

        try:
            dtes = getter(symbol)
        except Exception:
            return []

        try:
            return sorted(int(x) for x in dtes)
        except Exception:
            return []

    def _select_width_for_task(
        self,
        strategy: Any,
        underlying_hint: Optional[float],
    ) -> Optional[float]:
        width_rule = getattr(strategy, "width_rule", None)
        if width_rule is None or underlying_hint is None:
            return None

        try:
            width = choose_width(width_rule, float(underlying_hint))
        except Exception:
            width = None

        return width

    def _passes_strategy_filters(
        self,
        candidate: Dict[str, Any],
        strategy: Any,
    ) -> bool:
        decision = self._evaluate_strategy_filters(candidate, strategy)
        return decision.passed

    def _evaluate_strategy_filters(
        self,
        candidate: Dict[str, Any],
        strategy: Any,
    ) -> FilterDecision:
        filters = getattr(strategy, "filters", None)
        dte_rule = getattr(strategy, "dte", None)
        return evaluate_candidate_filters(candidate, filters, dte_rule)

    def _strategy_type_from_template(self, template: Any, dir_bias: str) -> str:
        """
        Map a StrategyTemplate into one of the internal strategy_type strings
        used by TradePlanner: 'iron_condor', 'short_put_spread',
        'short_call_spread', 'long_call_spread', 'long_put_spread', or 'skip'.

        This is intentionally tolerant of Enum values and naming conventions.
        """

        def _as_lower_str(val: Any) -> str:
            if isinstance(val, str):
                return val.lower()
            value = getattr(val, "value", None)
            if isinstance(value, str):
                return value.lower()
            return str(val).lower()

        name = getattr(template, "name", "") or ""
        name_l = name.lower()

        product_type_raw = getattr(template, "product_type", "")
        order_side_raw = getattr(template, "order_side", "")
        option_type_raw = getattr(template, "option_type", "")

        product_type = _as_lower_str(product_type_raw)
        order_side = _as_lower_str(order_side_raw)
        option_type = _as_lower_str(option_type_raw)

        if "iron_condor" in name_l:
            return "iron_condor"

        if "short_put_spread" in name_l:
            return "short_put_spread"

        if "short_call_spread" in name_l:
            return "short_call_spread"

        if "iron_fly" in name_l or "iron_bfly" in name_l:
            return "skip"

        if option_type == "put" and order_side == "short":
            return "short_put_spread"
        if option_type == "call" and order_side == "short":
            return "short_call_spread"
        if option_type == "call" and order_side == "long":
            return "long_call_spread"
        if option_type == "put" and order_side == "long":
            return "long_put_spread"

        return "skip"

    def _generate_for_symbol(
        self,
        symbol: str,
        row: Dict[str, Any],
        ta: Dict[str, Any],
        strategy_hint: str,
        dte_target: int,
    ) -> List[TradeIdea]:
        scores = ta.get("scores", {}) or {}
        structure = ta.get("structure", {}) or {}
        trend_regime = (ta.get("trend_regime") or {}).get("state", "unknown")
        vol_regime = (ta.get("vol_regime") or {}).get("state", "unknown")

        dir_bias = row.get(
            "ta_directional_bias", scores.get("directional_bias", "neutral")
        )
        vol_bias = row.get("ta_vol_bias", scores.get("vol_bias", "normal"))
        ta_score = scores.get("ta_bias", 0.0)

        support_levels: List[float] = structure.get("support") or []
        resistance_levels: List[float] = structure.get("resistance") or []
        range_info: Optional[Dict[str, Any]] = structure.get("range") or None

        strategy_type = self._pick_strategy_type(
            strategy_hint=strategy_hint,
            dir_bias=dir_bias,
            vol_bias=vol_bias,
            trend_regime=trend_regime,
        )
        if strategy_type == "skip":
            return []

        data_symbol, trade_symbol = resolve_symbols(symbol)

        underlying_hint = resolve_underlying_price_hint(
            symbol=trade_symbol,
            data_symbol=data_symbol,
            provider=self._get_provider_if_live(),
            ta_price_hint=self._infer_underlying_price_hint(
                support_levels,
                resistance_levels,
                range_info,
            ),
        )

        legs, spread_width = self._build_legs_from_ta(
            strategy_type=strategy_type,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            underlying_hint=underlying_hint,
            dte_target=dte_target,
            width_override=None,
        )

        if not legs:
            return []

        direction = self._direction_from_strategy(strategy_type, dir_bias)
        rationale, notes = self._build_rationale(
            symbol=symbol,
            strategy_type=strategy_type,
            dir_bias=dir_bias,
            vol_bias=vol_bias,
            trend_regime=trend_regime,
            ta_score=ta_score,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            vol_regime=vol_regime,
        )

        idea = TradeIdea(
            symbol=symbol,
            data_symbol=data_symbol,
            trade_symbol=trade_symbol,
            strategy=strategy_type,
            direction=direction,
            vol_context=vol_bias,
            rationale=rationale,
            legs=legs,
            underlying_price_hint=underlying_hint,
            dte_target=dte_target,
            spread_width=spread_width,
            target_delta=0.20,
            notes=notes,
        )
        return [idea]

    def _pick_strategy_type(
        self,
        strategy_hint: str,
        dir_bias: str,
        vol_bias: str,
        trend_regime: str,
    ) -> str:
        """
        Decide a coarse strategy type using the indicated style + TA context.

        Returns one of:
          - "iron_condor"
          - "short_put_spread"
          - "short_call_spread"
          - "long_call_spread"
          - "long_put_spread"
          - "skip"
        """
        strategy_hint = (strategy_hint or "").lower()

        if "bullish" in dir_bias:
            directional = "bullish"
        elif "bearish" in dir_bias:
            directional = "bearish"
        else:
            directional = "neutral"

        # 1) Range short premium
        if strategy_hint == "short_premium_range":
            if directional == "neutral":
                return "iron_condor"
            if directional == "bullish":
                return "short_put_spread"
            if directional == "bearish":
                return "short_call_spread"

        # 2) Trend short premium
        if strategy_hint == "short_premium_trend":
            if directional == "bullish":
                return "short_put_spread"
            if directional == "bearish":
                return "short_call_spread"
            if trend_regime in ("uptrend", "downtrend"):
                return "skip"

        # 3) Long premium breakout
        if strategy_hint == "long_premium_breakout":
            if directional == "bullish":
                return "long_call_spread"
            if directional == "bearish":
                return "long_put_spread"
            if vol_bias == "expansion_likely":
                return "skip"

        return "skip"

    def _infer_underlying_price_hint(
        self,
        support_levels: List[float],
        resistance_levels: List[float],
        range_info: Optional[Dict[str, Any]],
    ) -> Optional[float]:
        """
        Approximate current price using TA structure only.
        Later this can be replaced with actual last price from chains/quotes.
        """
        if range_info and "low" in range_info and "high" in range_info:
            low = range_info["low"]
            high = range_info["high"]
            if high > low:
                return (low + high) / 2.0

        if support_levels and resistance_levels:
            return (support_levels[-1] + resistance_levels[0]) / 2.0

        if resistance_levels:
            return resistance_levels[-1]
        if support_levels:
            return support_levels[-1]

        return None

    def _build_legs_from_ta(
        self,
        strategy_type: str,
        support_levels: List[float],
        resistance_levels: List[float],
        underlying_hint: Optional[float],
        dte_target: int,
        width_override: Optional[float] = None,
    ) -> Tuple[List[TradeLeg], float]:
        """
        Build a logical vertical/IC from TA levels.

        This does not yet resolve to actual strikes in a real option chain.
        """
        legs: List[TradeLeg] = []

        def nearest_below(levels: List[float], ref: float) -> Optional[float]:
            below = [lvl for lvl in levels if lvl < ref]
            if below:
                return below[-1]
            return None

        def nearest_above(levels: List[float], ref: float) -> Optional[float]:
            above = [lvl for lvl in levels if lvl > ref]
            if above:
                return above[0]
            return None

        expiry_str = f"{dte_target}DTE"  # placeholder label

        if underlying_hint is None:
            # crude fallback
            if support_levels:
                underlying_hint = support_levels[-1]
            elif resistance_levels:
                underlying_hint = resistance_levels[0]
            else:
                return [], 0.0

        ref_price = float(underlying_hint)

        if width_override is not None and width_override > 0:
            spread_width = float(width_override)
        else:
            spread_width = max(ref_price * 0.01, 0.5)

        if strategy_type == "iron_condor":
            short_put_strike = nearest_below(support_levels, ref_price) or (
                ref_price - spread_width
            )
            short_call_strike = nearest_above(resistance_levels, ref_price) or (
                ref_price + spread_width
            )

            long_put_strike = short_put_strike - spread_width
            long_call_strike = short_call_strike + spread_width

            legs = [
                TradeLeg(
                    side="short",
                    type="put",
                    strike=float(short_put_strike),
                    expiry=expiry_str,
                ),
                TradeLeg(
                    side="long",
                    type="put",
                    strike=float(long_put_strike),
                    expiry=expiry_str,
                ),
                TradeLeg(
                    side="short",
                    type="call",
                    strike=float(short_call_strike),
                    expiry=expiry_str,
                ),
                TradeLeg(
                    side="long",
                    type="call",
                    strike=float(long_call_strike),
                    expiry=expiry_str,
                ),
            ]

        elif strategy_type == "short_put_spread":
            short_strike = nearest_below(support_levels, ref_price) or (
                ref_price - spread_width
            )
            long_strike = short_strike - spread_width
            legs = [
                TradeLeg(
                    side="short",
                    type="put",
                    strike=float(short_strike),
                    expiry=expiry_str,
                ),
                TradeLeg(
                    side="long",
                    type="put",
                    strike=float(long_strike),
                    expiry=expiry_str,
                ),
            ]

        elif strategy_type == "short_call_spread":
            short_strike = nearest_above(resistance_levels, ref_price) or (
                ref_price + spread_width
            )
            long_strike = short_strike + spread_width
            legs = [
                TradeLeg(
                    side="short",
                    type="call",
                    strike=float(short_strike),
                    expiry=expiry_str,
                ),
                TradeLeg(
                    side="long",
                    type="call",
                    strike=float(long_strike),
                    expiry=expiry_str,
                ),
            ]

        elif strategy_type == "long_call_spread":
            long_strike = ref_price
            short_strike = long_strike + spread_width
            legs = [
                TradeLeg(
                    side="long",
                    type="call",
                    strike=float(long_strike),
                    expiry=expiry_str,
                ),
                TradeLeg(
                    side="short",
                    type="call",
                    strike=float(short_strike),
                    expiry=expiry_str,
                ),
            ]

        elif strategy_type == "long_put_spread":
            long_strike = ref_price
            short_strike = long_strike - spread_width
            legs = [
                TradeLeg(
                    side="long",
                    type="put",
                    strike=float(long_strike),
                    expiry=expiry_str,
                ),
                TradeLeg(
                    side="short",
                    type="put",
                    strike=float(short_strike),
                    expiry=expiry_str,
                ),
            ]

        return legs, spread_width

    def _direction_from_strategy(self, strategy_type: str, dir_bias: str) -> str:
        if strategy_type in ("short_put_spread", "long_call_spread"):
            return "bullish"
        if strategy_type in ("short_call_spread", "long_put_spread"):
            return "bearish"
        if strategy_type == "iron_condor":
            if "bullish" in dir_bias:
                return "slightly_bullish"
            if "bearish" in dir_bias:
                return "slightly_bearish"
            return "neutral"
        return "neutral"

    def _build_rationale(
        self,
        symbol: str,
        strategy_type: str,
        dir_bias: str,
        vol_bias: str,
        trend_regime: str,
        ta_score: float,
        support_levels: List[float],
        resistance_levels: List[float],
        vol_regime: str,
    ) -> Tuple[str, List[str]]:
        notes: List[str] = []

        if strategy_type == "iron_condor":
            rationale = (
                f"{symbol}: range / non-trending conditions with {vol_bias} volatility – "
                "structure favours short premium in both directions."
            )
            if support_levels and resistance_levels:
                notes.append(
                    f"Using support near {support_levels[-1]:.2f} and resistance near {resistance_levels[0]:.2f} "
                    "as anchors for IC wings."
                )
        elif strategy_type == "short_put_spread":
            rationale = (
                f"{symbol}: {dir_bias} with downside levels providing support – "
                "bullish short put spread structured below key support."
            )
        elif strategy_type == "short_call_spread":
            rationale = (
                f"{symbol}: {dir_bias} with upside resistance zones – "
                "bearish short call spread structured above key resistance."
            )
        elif strategy_type == "long_call_spread":
            rationale = (
                f"{symbol}: {dir_bias} with potential breakout and {vol_bias} volatility – "
                "bullish debit spread to participate in upside."
            )
        elif strategy_type == "long_put_spread":
            rationale = (
                f"{symbol}: {dir_bias} with downside continuation risk – "
                "bearish debit spread to participate in further downside."
            )
        else:
            rationale = f"{symbol}: no clear structured trade idea."

        notes.append(
            f"Trend regime: {trend_regime}, TA bias: {ta_score:.2f}, vol regime: {vol_regime}."
        )
        notes.append(f"Volatility context (for options): {vol_bias}.")
        return rationale, notes
