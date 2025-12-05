# StratDeck Codex-Max Task — Daily Open-Cycle Orchestrator (Paper-Only)

## Task ID

`STRATDECK-TASK-open-cycle-orchestrator`

## Repository & Environment

- **Repo URL:** `git@github.com:theglove44/stratdeck.git`
- **Local clone path (assumed):** `~/Projects/stratdeck-copilot`
- **Primary language:** Python 3.9+
- **Test command:** `pytest -q`
- **CLI entrypoint:** `python -m stratdeck.cli`
- **Data modes (env var):**
  - `STRATDECK_DATA_MODE=mock`
  - `STRATDECK_DATA_MODE=live`

Always work from the project root (`~/Projects/stratdeck-copilot`).

## High-Level Goal

Implement the **first orchestrator slice** for StratDeck:

> A daily “open-cycle” orchestrator that:
>
> 1. Generates trade ideas for a given universe + strategy.
> 2. Vets each idea using the existing vetting engine.
> 3. Selects a subset based on verdict and score.
> 4. Opens those trades in the existing **paper trading engine** only.
> 5. Emits a clear, inspectable summary via both a **Python API** and a **CLI command**.

The orchestrator **must reuse existing core logic**:

- Trade idea generation (the engine behind `trade-ideas`).
- Vetting (the engine behind `ideas-vet`).
- Paper trading engine (the engine behind `enter-auto` / `enter_paper_trade`).

No duplication of rules / logic; only orchestration code should be added.

## Constraints & Principles

1. **Do not re-implement existing features.** Always call existing core functions behind:
   - `trade-ideas` CLI
   - `ideas-vet` CLI
   - paper trading / positions store

2. **Pure orchestration:** The new Python API for the open cycle must:
   - Contain **no CLI parsing**.
   - Avoid `print` logging (beyond existing project style).
   - Be composable and testable in isolation.

3. **Paper-only:** This slice **must not** place live trades. It should always use the existing **paper** engine (`enter_paper_trade`) and positions store.

4. **Testable:** All changes must be fully covered by `pytest -q` with no failures.

5. **Self-healing:** If any errors arise (import errors, test failures, CLI bugs), you must:
   - Diagnose the cause.
   - Adjust the implementation.
   - Re-run tests until they pass or you hit a hard constraint.

---

## Existing Architecture (To Reuse)

You MUST inspect these modules before making changes:

- `stratdeck/agents/trade_planner.py`
  - Contains `class TradeIdea` (the core trade idea model).
  - Contains the core logic that `trade-ideas` uses to scan universes, pull chains, and emit `TradeIdea`s.

- `stratdeck/strategy_engine.py`
  - Strategy/universe task bridge used by the trade-ideas engine.

- `stratdeck/strategies.py`
  - Houses declarative strategy templates and likely `StrategyRuleSnapshot` or equivalent.
  - Whatever `ideas-vet` uses to derive rules from a `strategy_id` must be reused.

- `stratdeck/vetting.py`
  - `class IdeaVetting(BaseModel)` with fields:
    - `score: float`
    - `verdict: VetVerdict`
    - `rationale: str`
    - `reasons: List[str]`
  - `VetVerdict` enum with `.ACCEPT`, `.BORDERLINE`, `.REJECT`.
  - Functions:
    - `build_vetting_inputs(...)`
    - `vet_from_inputs(...)`
    - `vet_single_idea(idea, rules) -> IdeaVetting`
    - A batch helper returning `List[Tuple[Any, IdeaVetting]]` (around line ~385).
  - These are the **only** way you should compute vetting.

- `stratdeck/tools/orders.py`
  - Paper trading / order helpers, including:
    - `enter_paper_trade(...)`:
      - Writes to the positions store (paper ledger).
      - Returns a dict with `"position_id"` field.
    - `place_paper(...)` and preview helpers.
  - Guard rails ensure paper vs live: this task stays on **paper**.

- `stratdeck/tools/positions.py`
  - `class PaperPosition(BaseModel)`
  - `class PositionsStore`
  - `POS_PATH` (path to the JSON ledger).
  - Utility: `_position_from_plan(...)`, `_legacy_dict(...)`.

- `stratdeck/cli.py`
  - CLI group and commands:
    - `@cli.command(name="trade-ideas")`
    - `@cli.command(name="ideas-vet")`
    - `enter-auto` (auto-enter from last trade ideas, paper-only).
  - Uses `LAST_TRADE_IDEAS_PATH` and `.stratdeck/last_trade_ideas.json`.
  - Uses `PaperPosition`, `PositionsStore`, and `orders.enter_paper_trade(...)`.

- `stratdeck/orchestrator.py`
  - Already contains narrative and/or shell-based orchestration that shells out to:
    - `trade-ideas`
    - `TraderAgent.enter_from_idea` (paper/live).
  - This is the natural home for the new **pure-Python open cycle** API.

You must respect this structure and extend it, not replace it.

---

## Target Deliverables

### 1. Core Python API (in `stratdeck/orchestrator.py`)

Add:

1. `OpenedPositionSummary` dataclass
2. `OpenCycleResult` dataclass
3. Helper functions for selection:
   - `is_eligible(vetting: IdeaVetting, min_score: float) -> bool`
   - `select_trades(...)`
4. Main orchestrator function:
   - `run_open_cycle(...)` (see signature below)
   - Uses dependency injection for idea generation, vetting, and paper entry (for testability).

#### 1.1 Data structures

In `stratdeck/orchestrator.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence, Tuple, List

from stratdeck.agents.trade_planner import TradeIdea
from stratdeck.vetting import IdeaVetting, VetVerdict
from stratdeck.tools.positions import PaperPosition
```

Add:

```python
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
    generated_count: int       # total ideas generated
    eligible_count: int        # count that passed filters (before max_trades slice)
    opened: List[OpenedPositionSummary]
```

#### 1.2 Selection helpers

```python
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
```

This will later be extended for per-symbol caps, account risk, regime rules, etc.

#### 1.3 Default adapter to paper engine

Still in `stratdeck/orchestrator.py`, define a helper that mirrors `enter-auto` CLI logic:

```python
from stratdeck.tools.orders import enter_paper_trade
from stratdeck.tools.positions import PositionsStore, POS_PATH, PaperPosition

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
```

#### 1.4 Main `run_open_cycle` API

Add:

```python
def run_open_cycle(
    universe: str,
    strategy: str,
    max_trades: int,
    min_score: float,
    *,
    qty: int = 1,
    idea_generator: Callable[[str, str], List[TradeIdea]] | None = None,
    vet_one: Callable[[TradeIdea, "StrategyRuleSnapshot"], IdeaVetting] | None = None,
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
    # Wire defaults to existing implementations if not overridden
    if idea_generator is None:
        # Implement or reuse a function in agents.trade_planner that the
        # trade-ideas CLI uses internally, e.g. generate_trade_ideas(universe, strategy)
        from stratdeck.agents.trade_planner import generate_trade_ideas
        idea_generator = generate_trade_ideas

    if vet_one is None:
        # Use the same function + rules snapshot that ideas-vet CLI uses
        from stratdeck.vetting import vet_single_idea
        vet_one = vet_single_idea

    if open_from_idea is None:
        open_from_idea = _open_paper_position_from_idea

    # Build strategy rule snapshot exactly as ideas-vet does
    from stratdeck.strategies import StrategyRuleSnapshot

    rules = StrategyRuleSnapshot.for_id(strategy)  # or the equivalent method that ideas-vet uses

    # 1) Generate ideas
    ideas = idea_generator(universe, strategy)

    # 2) Vet each idea
    vetted_pairs: List[Tuple[TradeIdea, IdeaVetting]] = []
    for idea in ideas:
        vet_result = vet_one(idea, rules)
        vetted_pairs.append((idea, vet_result))

    # 3–4) Compute eligible and selection
    eligible_pairs = [
        (idea, vet) for idea, vet in vetted_pairs if is_eligible(vet, min_score)
    ]
    selected_pairs = select_trades(
        vetted_pairs,
        max_trades=max_trades,
        min_score=min_score,
    )

    # 5–6) Open positions via paper engine
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

    # 7) Build result
    return OpenCycleResult(
        universe=universe,
        strategy=strategy,
        generated_count=len(ideas),
        eligible_count=len(eligible_pairs),
        opened=opened,
    )
```

**Important:**

- You must inspect how `ideas-vet` gets `StrategyRuleSnapshot` in `stratdeck/cli.py` and mirror that logic exactly (method name `for_id` is illustrative; use the real one).
- You must implement or reuse a `generate_trade_ideas(universe, strategy)` function in `agents.trade_planner`:
  - Extract the current `trade-ideas` CLI internals into this function.
  - The new API and the `trade-ideas` CLI must both call this.

---

### 2. CLI Command: `open-cycle`

Add a new Click command to `stratdeck/cli.py` that uses `run_open_cycle`.

#### 2.1 Command signature

- Command name: `open-cycle`
- Options:
  - `--universe` (required): e.g. `index_core`, `tasty_watchlist_chris_historical_trades`.
  - `--strategy` / `strategy_id` (required): e.g. `short_put_spread_index_45d`, `short_put_spread_equity_45d`.
  - `--max-trades` (int, default: `3`).
  - `--min-score` (float, default: `80.0`).
  - `--qty` (int, default: `1`): contracts per idea.
  - `--json-output` (flag).

#### 2.2 Implementation sketch

In `stratdeck/cli.py`:

```python
import json

from stratdeck.orchestrator import run_open_cycle

@cli.command(name="open-cycle")
@click.option(
    "--universe",
    required=True,
    help="Universe name (e.g. index_core, tasty_watchlist_chris_historical_trades).",
)
@click.option(
    "--strategy",
    "strategy_id",
    required=True,
    help="Strategy ID (e.g. short_put_spread_index_45d).",
)
@click.option(
    "--max-trades",
    type=int,
    default=3,
    show_default=True,
    help="Maximum number of trades to open in this cycle.",
)
@click.option(
    "--min-score",
    type=float,
    default=80.0,
    show_default=True,
    help="Minimum vetting score to be eligible.",
)
@click.option(
    "--qty",
    type=int,
    default=1,
    show_default=True,
    help="Contract quantity per trade.",
)
@click.option(
    "--json-output",
    is_flag=True,
    default=False,
    help="Emit JSON instead of human-readable output.",
)
def open_cycle(
    universe: str,
    strategy_id: str,
    max_trades: int,
    min_score: float,
    qty: int,
    json_output: bool,
) -> None:
    """Run the daily open-cycle orchestrator (paper-only)."""
    result = run_open_cycle(
        universe=universe,
        strategy=strategy_id,
        max_trades=max_trades,
        min_score=min_score,
        qty=qty,
    )

    if json_output:
        payload = []
        for opened in result.opened:
            idea = opened.idea
            vet = opened.vetting
            pos = opened.position

            payload.append(
                {
                    "universe": result.universe,
                    "strategy": result.strategy,
                    "idea": idea.model_dump(mode="json"),
                    "vetting": {
                        "score": vet.score,
                        "verdict": vet.verdict.value,
                        "rationale": vet.rationale,
                        "reasons": vet.reasons,
                    },
                    "position": pos.model_dump(mode="json"),
                    "opened_at": opened.opened_at.isoformat(),
                }
            )

        click.echo(json.dumps(payload, indent=2))
        return

    total = result.generated_count
    eligible = result.eligible_count
    opened_count = len(result.opened)

    click.echo(
        f"[open-cycle] universe={result.universe} strategy={result.strategy} "
        f"ideas={total} eligible={eligible} opened={opened_count}"
    )

    if opened_count == 0:
        click.echo("[open-cycle] No trades opened (verdict/score filters blocked everything).")
        return

    header = f"{'Symbol':<8} {'Strategy':<30} {'Score':>6} {'Verdict':>9} {'Qty':>4}"
    click.echo(header)
    click.echo("-" * len(header))

    for opened in result.opened:
        idea = opened.idea
        vet = opened.vetting
        click.echo(
            f"{idea.symbol:<8} "
            f"{idea.strategy_id:<30} "
            f"{vet.score:>6.1f} "
            f"{vet.verdict.value:>9} "
            f"{qty:>4}"
        )
```

#### 2.3 Example invocations

Mock mode smoke tests:

```bash
cd ~/Projects/stratdeck-copilot
export STRATDECK_DATA_MODE=mock

python -m stratdeck.cli open-cycle   --universe index_core   --strategy short_put_spread_index_45d   --max-trades 2   --min-score 80
```

JSON mode:

```bash
python -m stratdeck.cli open-cycle   --universe tasty_watchlist_chris_historical_trades   --strategy short_put_spread_equity_45d   --max-trades 3   --min-score 85   --qty 1   --json-output
```

---

### 3. Trade-Ideas Core Extraction (if needed)

If not already present, you MUST extract a pure function in `stratdeck/agents/trade_planner.py` that drives the existing `trade-ideas` CLI:

```python
def generate_trade_ideas(universe: str, strategy_id: str) -> List[TradeIdea]:
    """
    Strategy-aware generator used by both:

    - trade-ideas CLI
    - run_open_cycle orchestrator
    """
    # Implementation should refactor existing CLI internals:
    # - Use strategy_engine/strategies to build scan tasks
    # - Pull chains from Tastytrade adapter
    # - Build and return a list of TradeIdea instances
```

Then:

- Update `stratdeck/cli.py` `trade-ideas` command to call `generate_trade_ideas(...)` rather than inlining logic.
- Ensure the behaviour is unchanged (run CLI tests).

---

## Testing Plan

You must add tests for:

1. **Core orchestrator logic** (unit tests).
2. **Integration** with real idea generator, vetting, and paper engine in `mock` mode.
3. **CLI** behaviour for `open-cycle` (human + JSON output).

### 1. Core orchestrator tests

**File suggestion:** `tests/test_open_cycle_core.py`

Create tests that **inject fake dependencies** for:

- `idea_generator`
- `vet_one`
- `open_from_idea`

#### Test: filters by verdict and score

- Setup:
  - Create 3 `TradeIdea` instances with distinct `strategy_id`s.
  - `fake_ideas` returns all three for any universe/strategy.
  - `fake_vet` returns `IdeaVetting` with:

    | Idea | score | verdict              |
    |------|--------|---------------------|
    | A    | 90     | `VetVerdict.ACCEPT` |
    | B    | 95     | `VetVerdict.BORDERLINE` |
    | C    | 70     | `VetVerdict.ACCEPT` |

  - `fake_open_from_idea` appends ideas to a list and returns a dummy `PaperPosition`.

- Call:

  ```python
  result = run_open_cycle(
      universe="U",
      strategy="S",
      max_trades=5,
      min_score=80,
      idea_generator=fake_ideas,
      vet_one=fake_vet,
      open_from_idea=fake_open_from_idea,
  )
  ```

- Assertions:
  - Only idea A is passed to `fake_open_from_idea`.
  - `result.generated_count == 3`.
  - `result.eligible_count == 1`.
  - `len(result.opened) == 1` and `result.opened[0].idea` is idea A.

#### Test: respects `max_trades` and sorts by score

- Setup:
  - Three ACCEPT ideas with scores 70, 85, 95.
  - `max_trades=2`, `min_score=0`.
- Assert:
  - `open_from_idea` called exactly twice.
  - The two highest scores are selected (check order).

#### Test: no eligible ideas → no opens

- All vettings either `VetVerdict.REJECT` or score below `min_score`.
- Assert:
  - `len(result.opened) == 0`.
  - `open_from_idea` not called.

### 2. Integration tests (mock data mode)

**File suggestion:** `tests/test_open_cycle_integration.py`

Use **real** idea generator, vetting, and paper engine in `mock` mode:

```python
import os

from stratdeck.orchestrator import run_open_cycle
from stratdeck.tools.positions import PaperPosition

def test_open_cycle_mock_mode_runs(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")

    result = run_open_cycle(
        universe="index_core",
        strategy="short_put_spread_index_45d",
        max_trades=1,
        min_score=0,
    )

    assert result.generated_count >= 0
    assert len(result.opened) <= 1

    for opened in result.opened:
        assert opened.idea.symbol
        assert isinstance(opened.vetting.score, (int, float))
        assert isinstance(opened.position, PaperPosition)
```

If mock data sometimes yields zero eligible trades, allow `len(result.opened) == 0` but still assert shapes.

### 3. CLI tests

**File suggestion:** `tests/test_open_cycle_cli.py`

Leverage existing CLI testing style with `CliRunner`:

```python
import json
from click.testing import CliRunner

from stratdeck.cli import cli

def test_open_cycle_cli_human(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "open-cycle",
            "--universe", "index_core",
            "--strategy", "short_put_spread_index_45d",
            "--max-trades", "1",
            "--min-score", "0",
        ],
    )

    assert result.exit_code == 0
    assert "[open-cycle]" in result.output
    assert "opened=" in result.output


def test_open_cycle_cli_json(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "open-cycle",
            "--universe", "index_core",
            "--strategy", "short_put_spread_index_45d",
            "--max-trades", "1",
            "--min-score", "0",
            "--json-output",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    if data:
        item = data[0]
        assert "idea" in item
        assert "vetting" in item
        assert "position" in item
        assert "opened_at" in item
```

Optionally, for deterministic CLI tests, monkeypatch `run_open_cycle` to return a fixed `OpenCycleResult`.

---

## Workflow Instructions (Branching, Build, Self-Fix Loop)

### 1. Branching

From repo root:

```bash
cd ~/Projects/stratdeck-copilot
git status
git checkout main
git pull origin main
git checkout -b feature/open-cycle-orchestrator
```

### 2. Implement Changes

1. Inspect `stratdeck/cli.py`, `agents/trade_planner.py`, `vetting.py`, `strategies.py`, `tools/orders.py`, `tools/positions.py`, `orchestrator.py`.
2. Extract or confirm a `generate_trade_ideas(universe, strategy_id)` function in `agents.trade_planner` used by `trade-ideas` CLI.
3. Implement `OpenedPositionSummary`, `OpenCycleResult`, `is_eligible`, `select_trades`, `_open_paper_position_from_idea`, and `run_open_cycle` in `stratdeck/orchestrator.py` as described.
4. Wire a new `open-cycle` command in `stratdeck/cli.py` that calls `run_open_cycle`.
5. Add new tests as described.

### 3. Formatting & Linting

If the repo uses tools like `black`, `ruff`, or `isort`, detect them from `pyproject.toml` or `requirements-dev.txt` and run them. For example (only if present):

```bash
black stratdeck tests
ruff check stratdeck tests
isort stratdeck tests
```

Fix any style or lint errors by editing files and re-running the tools.

### 4. Test & Self-Fix Loop

Run:

```bash
pytest -q
```

If **any tests fail**:

1. Read the failure message carefully.
2. Identify whether the issue is:
   - import error,
   - type/attribute mismatch,
   - CLI behaviour mismatch,
   - assertion mismatch,
   - or a regression in existing features.
3. Modify the implementation to fix the problem:
   - Align imports/types with real project names.
   - Update selection logic if tests assert slightly different semantics.
   - Fix any incorrect assumptions about `StrategyRuleSnapshot` or idea generator.
4. Re-run `pytest -q` after each fix.
5. Repeat until **all tests pass** or you hit a genuine external constraint.

Then run **smoke tests**:

```bash
export STRATDECK_DATA_MODE=mock

python -m stratdeck.cli trade-ideas   --universe index_core   --strategy short_put_spread_index_45d   --json-output .stratdeck/last_trade_ideas.json

python -m stratdeck.cli ideas-vet   --ideas-path .stratdeck/last_trade_ideas.json

python -m stratdeck.cli open-cycle   --universe index_core   --strategy short_put_spread_index_45d   --max-trades 1   --min-score 0

python -m stratdeck.cli open-cycle   --universe index_core   --strategy short_put_spread_index_45d   --max-trades 1   --min-score 0   --json-output
```

Ensure:

- No tracebacks.
- Output shape matches expectations.

### 5. Git Commit

When tests pass and smoke tests look good:

```bash
git status
git add stratdeck/orchestrator.py stratdeck/cli.py stratdeck/agents/trade_planner.py tests
git diff --cached
git commit -m "Add open-cycle orchestrator for paper positions"
git push -u origin feature/open-cycle-orchestrator
```

### 6. Final Output Summary

At the end of the task, produce a summary including:

- New/changed files.
- New public API:
  - `run_open_cycle(...)`
  - `OpenCycleResult`, `OpenedPositionSummary`
  - `generate_trade_ideas(...)` (if you had to extract it)
- CLI usage examples for `open-cycle`.
- Confirmation that `pytest -q` passes.
- Any caveats (e.g. mock-data limitations).

---

## Acceptance Criteria

This task is **complete** when:

1. `pytest -q` passes with no failures.
2. `python -m stratdeck.cli open-cycle ...` works in `STRATDECK_DATA_MODE=mock` and:
   - Generates ideas,
   - Vets them,
   - Opens up to `max_trades` paper positions,
   - Outputs:
     - Human-readable counts in normal mode.
     - JSON with `idea`, `vetting`, `position`, and `opened_at` in `--json-output` mode.
3. The orchestrator logic **reuses existing core components** (no duplication of trade-ideas or vetting logic).
4. Only **paper** paths are used; no live trading wiring is introduced.
5. The changes are encapsulated in a feature branch with clean commits and are ready for PR/merge to `main`.
