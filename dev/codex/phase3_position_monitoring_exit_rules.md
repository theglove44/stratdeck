# PROJECT: StratDeck Agent System – Phase 3: Position Monitoring & Exit Rules

## High-Level Goal

Implement a **paper-only position monitoring and exit rule engine** on top of existing StratDeck mechanics. The engine should:

- Load open `PaperPosition`s from `.stratdeck/positions.json`.
- Use live / recent market data (Tastytrade chains/quotes + IVR) to compute:
  - Current unrealised P&L.
  - % of max profit / % of max loss (for defined-risk strategies).
  - DTE (days to expiry).
  - IV / IVR context.
- Apply **mechanical, strategy-specific exit rules**:
  - Default: **exit at profit target OR at 21 DTE**, whichever comes first.
  - For **short-premium strategies**, if **IVR < 20** → suggest exiting.
- Phase 3 is **paper-only**:
  - No live broker orders.
  - All exits are simulated via updates to `.stratdeck/positions.json`.
  - Record exit metadata (`closed_at`, `realized_pl`, `exit_reason`, etc.).

This spec must **not** break Phase 1 (`trade-ideas`) or Phase 2 (`enter-auto`, `PaperPosition`, `PositionsStore`, `positions list`).

---

## Current State (Reference)

### Phase 1 – Trade Ideas

- CLI:

  ```bash
  python -m stratdeck.cli trade-ideas     --universe index_core     --strategy short_put_spread_index_45d     --json-output
  ```

- Behaviour:
  - Produces stable JSON of trade ideas.
  - Includes `strategy_id`, `universe_id`, `filters_passed`, `filters_applied`, `filter_reasons`, `underlying_price_hint`.
  - Writes `.stratdeck/last_trade_ideas.json` (matches stdout).
  - XSP/SPX scaling and vol hints are reliable; filters “bite” and are explainable.

### Phase 2 – Paper Trading Engine

- CLI:

  ```bash
  python -m stratdeck.cli enter-auto
  ```

- Behaviour:
  - Reads `.stratdeck/last_trade_ideas.json`.
  - Picks idea at index 0.
  - Uses live chains (Tastytrade) to get leg mids.
  - Computes `entry_mid` / entry credit.
  - Creates and persists `PaperPosition` to `.stratdeck/positions.json`.

- `PaperPosition` / `PositionsStore`:
  - JSON-backed store at `.stratdeck/positions.json`.
  - Each position has:
    - `id` (UUID)
    - `symbol`, `trade_symbol`
    - `strategy_id`, `universe_id`
    - `direction`
    - `qty`
    - `entry_mid`
    - Lifecycle fields: at least `status="open"`, `opened_at`, etc.

- `positions` CLI:
  - `positions list` / `positions list --json-output` read from `.stratdeck/positions.json` and show open positions.

---

## New Behaviour – Phase 3 Overview

We are adding a **position monitoring and exit decision** layer, driven by:

1. **Strategy mechanics (canonical tasty-style rules)**

   For each strategy:

   | Strategy              | Winner Management                          | Loss Handling / Notes                           |
   | --------------------- | ------------------------------------------ | ----------------------------------------------- |
   | Short Strangle        | Exit at ~50% of initial credit or 21 DTE  | Undefined risk → adjust/roll if risk develops  |
   | Iron Condor           | Exit at ~50% of initial credit or 21 DTE  | Defined risk, often no active loss management  |
   | Credit Spread         | Exit at ~50% of initial credit or 21 DTE  | Defined risk, let max loss happen if it hits   |
   | Ratio Spread          | Exit at ~30% of max profit                 | Roll/adjust if trade goes against you          |
   | Broken Wing Butterfly | Exit at ~25% of max profit                 | No adjustments on loss side                    |
   | Diagonal Spread       | Exit at ~25% of max profit                 | Roll near/short leg forward if trade goes bad  |

   **Mechanical default:** For all of these, Phase 3 should implement:

   - **Exit when either:**
     - Profit target is hit (percentage of initial credit or of max profit), OR
     - **DTE ≤ 21** (mechanical “21 DTE” exit).

2. **IVR (IV Rank) rules**

   - Use **IV Rank (IVR)** as the primary vol context.
   - Thresholds (canonical):

     - `IVR > 50` → Excellent environment for short premium.
     - `IVR > 30` → Acceptable environment for short premium.
     - `20 <= IVR < 30` → “Meh” zone; no clear edge.
     - `IVR < 20` → Generally avoid short premium.
     - `IVR < 10` → Very poor environment for short premium.

   - **Phase 3 overlay rule:**
     - For **short premium strategies**, if **IVR < 20**:
       - The monitor should **suggest exiting** open positions in that underlying.
       - This is a **soft rule**: default behaviour is “exit candidate with reason IVR_BELOW_20”, but we can later add a config flag for “auto-close on IVR soft exit”.

3. **DTE behaviour**

   - Entry DTE (45/30–45/60–90) is already codified in strategy design; we **don’t change entry**.
   - Phase 3 must enforce **21 DTE as a mechanical exit backstop** on open positions, per strategy mechanics table.

---

## Design: New Concepts & Data Flow

### 1. New Models

#### 1.1 `PositionMetrics`

Create a new Pydantic v2 model representing a **snapshot of a position with live data**.

**File:** `stratdeck/tools/position_monitor.py` (new module)

```python
from datetime import datetime
from pydantic import BaseModel

class PositionMetrics(BaseModel):
    position_id: str
    symbol: str
    trade_symbol: str
    strategy_id: str
    universe_id: str

    # Pricing / P&L
    underlying_price: float
    entry_mid: float                # from PaperPosition
    current_mid: float              # current spread mid (or synthetic)
    unrealized_pl_per_contract: float
    unrealized_pl_total: float      # per contract * 100 * qty (for options)

    # Max profit / loss (if defined-risk)
    max_profit_per_contract: float | None = None
    max_profit_total: float | None = None
    max_loss_per_contract: float | None = None
    max_loss_total: float | None = None

    pnl_pct_of_max_profit: float | None = None  # 0.5 == 50% of max profit
    pnl_pct_of_max_loss: float | None = None    # positive fraction of max loss taken

    # Time
    expiry: datetime
    dte: float                                # days to expiry (could be fractional)
    as_of: datetime                           # timestamp of metrics snapshot

    # Vol context
    iv: float | None = None
    ivr: float | None = None                 # IV Rank 0–100 or 0–1 depending on existing code

    # Strategy metadata
    is_short_premium: bool
    strategy_family: str                     # e.g. "short_strangle", "iron_condor", etc.
```

Implementation details:

- `current_mid` is computed via existing chain/pricing adapters:
  - For multi-leg spreads: use the same pricing logic used for `entry_mid` in `enter-auto`.
- `max_profit_*` and `max_loss_*`:
  - For **defined-risk credit spreads / iron condors**:
    - Can be computed from leg strikes and entry credit (width - credit for max loss, credit for max profit).
  - For **undefined risk strategies** (short strangles), `max_loss_*` may remain `None`.
- `pnl_pct_of_max_profit`:
  - Only computed if `max_profit_total` is not `None` and > 0.
  - Example for credit spread: `(unrealized_pl_total / max_profit_total)`.
- `pnl_pct_of_max_loss`:
  - Only meaningful for defined-risk; optional in Phase 3.

#### 1.2 `ExitRulesConfig`

Represents **strategy-level exit rules**, driven by config, not magic constants.

**File:** `stratdeck/tools/position_monitor.py` (same module, or separate `exit_rules.py` if preferred)

```python
from pydantic import BaseModel

class ExitRulesConfig(BaseModel):
    strategy_family: str                  # "short_strangle", "iron_condor", "credit_spread", "ratio_spread", "bwb", "diagonal"
    is_short_premium: bool

    # Profit target
    profit_target_basis: str              # "credit" or "max_profit"
    profit_target_pct: float              # e.g. 0.5 for 50%

    # Time-based rule
    dte_exit: int = 21                    # mechanical DTE exit floor

    # IVR-based suggestion (for short premium)
    ivr_soft_exit_below: float | None = 20.0

    # Optional: reserved for future per-strategy loss handling
    # e.g., "loss_management": "none" | "roll" | "adjust" ...
    loss_management_style: str | None = None
```

Notes:

- `strategy_family` abstracts multiple concrete strategy IDs into a small set of rule families (see mapping below).
- `profit_target_basis`:
  - `"credit"` for strategies where we target % of initial credit (short strangles, iron condors, credit spreads).
  - `"max_profit"` for ratio spreads, BWBs, diagonals.
- `ivr_soft_exit_below`:
  - For **short premium** strategies configure as `20.0`.
  - For non-short-premium strategies, may be `None`.

#### 1.3 `ExitDecision`

Represents the **outcome of rule evaluation** for a position at a given snapshot.

```python
from pydantic import BaseModel

class ExitDecision(BaseModel):
    position_id: str
    action: str                      # "hold" or "exit"
    reason: str                      # e.g. "TARGET_PROFIT_HIT", "DTE_BELOW_21", "IVR_BELOW_20"
    triggered_rules: list[str]       # human-readable rule descriptions
    notes: str | None = None
```

---

### 2. Config: Strategy Exit Rules

Introduce a dedicated config file for exit rules, keyed by `strategy_id` or by `strategy_family`.

**File:** `stratdeck/config/exits.yaml` (new)

Example contents (initial focus: strategies in table + current index spread strategy):

```yaml
defaults:
  short_premium:
    ivr_soft_exit_below: 20.0

strategies:
  # Current main strategy
  short_put_spread_index_45d:
    strategy_family: "credit_spread"
    is_short_premium: true
    profit_target_basis: "credit"
    profit_target_pct: 0.5        # 50% of initial credit
    dte_exit: 21
    ivr_soft_exit_below: 20.0
    loss_management_style: "none" # defined-risk, no active loss management

  # Template entries – future use
  short_strangle_index_45d:
    strategy_family: "short_strangle"
    is_short_premium: true
    profit_target_basis: "credit"
    profit_target_pct: 0.5
    dte_exit: 21
    ivr_soft_exit_below: 20.0
    loss_management_style: "roll_adjust"

  iron_condor_index_45d:
    strategy_family: "iron_condor"
    is_short_premium: true
    profit_target_basis: "credit"
    profit_target_pct: 0.5
    dte_exit: 21
    ivr_soft_exit_below: 20.0
    loss_management_style: "none"

  credit_spread_equity_30d:
    strategy_family: "credit_spread"
    is_short_premium: true
    profit_target_basis: "credit"
    profit_target_pct: 0.5
    dte_exit: 21
    ivr_soft_exit_below: 20.0
    loss_management_style: "none"

  ratio_spread_index_45d:
    strategy_family: "ratio_spread"
    is_short_premium: true         # net short premium on one side
    profit_target_basis: "max_profit"
    profit_target_pct: 0.3         # 30% of max profit
    dte_exit: 21
    ivr_soft_exit_below: 20.0
    loss_management_style: "roll_adjust"

  broken_wing_butterfly_index_45d:
    strategy_family: "bwb"
    is_short_premium: true
    profit_target_basis: "max_profit"
    profit_target_pct: 0.25        # 25% of max profit
    dte_exit: 21
    ivr_soft_exit_below: 20.0
    loss_management_style: "none"

  diagonal_spread_index:
    strategy_family: "diagonal"
    is_short_premium: false        # net long premium
    profit_target_basis: "max_profit"
    profit_target_pct: 0.25
    dte_exit: 21                    # applied to short leg expiry
    ivr_soft_exit_below: null
    loss_management_style: "roll_near_leg"
```

Implementation:

- Add a small loader in `stratdeck/tools/position_monitor.py` or `stratdeck/config/__init__.py`:

  ```python
  def load_exit_rules(strategy_id: str) -> ExitRulesConfig:
      # Load exits.yaml once, resolve suitable strategy block or fallback
      ...
  ```

- If no specific strategy entry is found:
  - Default to a safe generic config or raise a clear error in Phase 3 (explicit > silent).

---

### 3. Computation: Metrics & Rules

#### 3.1 Computing `PositionMetrics`

Add a function in `stratdeck/tools/position_monitor.py`:

```python
from .positions import PaperPosition  # existing
from .chain_pricing_adapter import price_position  # pseudocode – use existing pricing logic
from .vol import get_ivr_for_symbol  # pseudocode – reuse existing IV/IVR

def compute_position_metrics(
    position: PaperPosition,
    now: datetime,
    market_data_client,
    vol_client,
) -> PositionMetrics:
    """
    - Fetch underlying price and option chain data for `position.trade_symbol`.
    - Compute current spread mid using existing pricing/chain adapters.
    - Compute DTE from the position's expiry to `now`.
    - Fetch IV/IVR via vol client / existing vol utilities.
    - Compute max profit / max loss for defined-risk strategies.
    - Compute unrealised P&L and P&L as % of max profit.
    """

    # 1. Determine strategy_family, is_short_premium via ExitRulesConfig
    exit_rules = load_exit_rules(position.strategy_id)

    # 2. Use existing pricing logic to compute current_mid
    pricing = price_position(position, market_data_client)
    current_mid = pricing.mid

    # 3. Compute DTE and expiry from legs/position
    expiry = position.expiry  # assumed field on PaperPosition; adapt as needed
    dte = (expiry - now).total_seconds() / 86400.0

    # 4. Get vol context (IV/IVR)
    iv, ivr = vol_client.get_iv_and_ivr(position.symbol)

    # 5. Compute P&L and max profit/loss depending on strategy_family
    # (Implement helpers to calculate these from legs and entry_mid)
    max_profit_per_contract, max_loss_per_contract = compute_defined_risk_bounds(
        position, exit_rules.strategy_family
    )

    # Use qty and contract multiplier (assume 100 for standard options)
    qty = position.qty
    contract_mult = 100

    entry_mid = position.entry_mid
    unrealized_pl_per_contract = (entry_mid - current_mid) * contract_mult  # for credit spreads
    unrealized_pl_total = unrealized_pl_per_contract * qty

    max_profit_total = (
        max_profit_per_contract * contract_mult * qty
        if max_profit_per_contract is not None
        else None
    )
    max_loss_total = (
        max_loss_per_contract * contract_mult * qty
        if max_loss_per_contract is not None
        else None
    )

    pnl_pct_of_max_profit = (
        unrealized_pl_total / max_profit_total
        if max_profit_total and max_profit_total > 0
        else None
    )
    pnl_pct_of_max_loss = (
        unrealized_pl_total / max_loss_total
        if max_loss_total and max_loss_total != 0
        else None
    )

    return PositionMetrics(
        position_id=position.id,
        symbol=position.symbol,
        trade_symbol=position.trade_symbol,
        strategy_id=position.strategy_id,
        universe_id=position.universe_id,
        underlying_price=pricing.underlying_price,
        entry_mid=entry_mid,
        current_mid=current_mid,
        unrealized_pl_per_contract=unrealized_pl_per_contract,
        unrealized_pl_total=unrealized_pl_total,
        max_profit_per_contract=max_profit_per_contract,
        max_profit_total=max_profit_total,
        max_loss_per_contract=max_loss_per_contract,
        max_loss_total=max_loss_total,
        pnl_pct_of_max_profit=pnl_pct_of_max_profit,
        pnl_pct_of_max_loss=pnl_pct_of_max_loss,
        expiry=expiry,
        dte=dte,
        as_of=now,
        iv=iv,
        ivr=ivr,
        is_short_premium=exit_rules.is_short_premium,
        strategy_family=exit_rules.strategy_family,
    )
```

> Codex to adapt `price_position`, `compute_defined_risk_bounds`, and `expiry` fields to actual code structure. The above is a template.

#### 3.2 Evaluating Exit Rules

Add a function in `stratdeck/tools/position_monitor.py`:

```python
def evaluate_exit_rules(
    metrics: PositionMetrics,
    rules: ExitRulesConfig,
) -> ExitDecision:
    triggered_rules: list[str] = []

    # 1. Profit target rule
    action = "hold"
    reason = "NONE"

    if rules.profit_target_basis == "credit":
        # Approximate profit_pct via unrealized_pl vs initial credit
        # For credit spreads: max_profit_total == initial_credit_total
        if metrics.max_profit_total and metrics.max_profit_total > 0:
            profit_pct = metrics.unrealized_pl_total / metrics.max_profit_total
            if profit_pct >= rules.profit_target_pct:
                action = "exit"
                reason = "TARGET_PROFIT_HIT"
                triggered_rules.append(
                    f"Profit target {rules.profit_target_pct:.0%} of credit reached "
                    f"({profit_pct:.1%})"
                )

    elif rules.profit_target_basis == "max_profit":
        if metrics.pnl_pct_of_max_profit is not None:
            if metrics.pnl_pct_of_max_profit >= rules.profit_target_pct:
                action = "exit"
                reason = "TARGET_PROFIT_HIT"
                triggered_rules.append(
                    f"Profit target {rules.profit_target_pct:.0%} of max profit "
                    f"reached ({metrics.pnl_pct_of_max_profit:.1%})"
                )

    # 2. DTE mechanical rule (21 DTE default)
    if metrics.dte <= rules.dte_exit:
        # If not already exiting for profit, we still want to exit due to time
        if action != "exit":
            action = "exit"
            reason = "DTE_BELOW_THRESHOLD"
        triggered_rules.append(
            f"DTE {metrics.dte:.1f} <= {rules.dte_exit} days – mechanical DTE exit"
        )

    # 3. IVR soft-exit rule for short premium
    if rules.is_short_premium and rules.ivr_soft_exit_below is not None:
        if metrics.ivr is not None and metrics.ivr < rules.ivr_soft_exit_below:
            # Soft signal – suggests exit, even if P&L target not hit
            if action != "exit":
                # Keep action "hold" for now, but mark as suggestion
                reason = "IVR_BELOW_SOFT_EXIT"
            triggered_rules.append(
                f"IVR {metrics.ivr:.1f} < {rules.ivr_soft_exit_below:.1f} "
                f"– short premium soft-exit environment"
            )

    # 4. Default reason if still none
    if reason == "NONE":
        reason = "HOLD"

    return ExitDecision(
        position_id=metrics.position_id,
        action=action,
        reason=reason,
        triggered_rules=triggered_rules,
        notes=None,
    )
```

Key points:

- **Profit target OR 21 DTE**:
  - Profit target check first; then DTE.
  - Even if profit target not hit, DTE rule will still trigger an exit.
- **IVR < 20**:
  - For short premium, the exit decision includes a **triggered rule** explaining IVR context.
  - Default behaviour: treat IVR as a **strong suggestion** (kept as metadata) but not forced auto-exit unless combined with profit/DTE rules.
  - Later, a config flag could upgrade IVR soft exit to hard exit.

---

## 4. Persistence & Model Extensions

### 4.1 Extend `PaperPosition` (Backward Compatible)

In the module where `PaperPosition` is defined (likely `stratdeck/tools/positions.py`), add **optional** fields:

```python
from datetime import datetime
from pydantic import BaseModel, Field

class PaperPosition(BaseModel):
    # existing fields...
    id: str
    symbol: str
    trade_symbol: str
    strategy_id: str
    universe_id: str
    direction: str
    qty: int
    entry_mid: float
    # existing lifecycle fields...
    status: str = "open"               # "open" or "closed"
    opened_at: datetime

    # NEW – lifecycle exit info
    closed_at: datetime | None = None
    exit_mid: float | None = None
    realized_pl_total: float | None = None
    exit_reason: str | None = None     # e.g. "TARGET_PROFIT_HIT", "DTE_BELOW_THRESHOLD", "MANUAL"

    # NEW – optional precomputed bounds (populated at entry or lazily)
    max_profit_total: float | None = None
    max_loss_total: float | None = None
```

Notes:

- All new fields have defaults or `None` → JSON compatibility preserved with existing `positions.json`.
- Codex should **not** change serialisation path used by Phase 2.

### 4.2 Updating `PositionsStore`

No major changes; ensure:

- When loading positions, Pydantic v2 handles missing fields gracefully.
- Add helper methods:

  ```python
  class PositionsStore:
      # existing methods...

      def get_open_positions(self) -> list[PaperPosition]:
          return [p for p in self.positions if p.status == "open"]

      def update_position(self, updated: PaperPosition) -> None:
          # in-place replace and save
          ...
  ```

---

## 5. CLI Changes – `positions` Subcommands

All changes must be implemented in `stratdeck/cli.py` (and/or underlying modules) **without** changing the behaviour of existing commands:

- Keep:
  - `positions list`
  - `positions list --json-output`

### 5.1 New Command: `positions monitor`

**Goal:** Compute metrics and exit decisions for all open positions and present a report.

**CLI signature:**

```bash
python -m stratdeck.cli positions monitor [--json-output]
```

**Behaviour:**

1. Load `.stratdeck/positions.json` via `PositionsStore`.
2. Filter `open_positions = store.get_open_positions()`.
3. For each open position:
   - Load `ExitRulesConfig` via `load_exit_rules(position.strategy_id)`.
   - Compute `PositionMetrics` via `compute_position_metrics(...)`.
   - Compute `ExitDecision` via `evaluate_exit_rules(...)`.
4. Collect into in-memory list:

   ```python
   items = [
       {
           "position": position.dict(),
           "metrics": metrics.dict(),
           "decision": decision.dict(),
       }
       for ...
   ]
   ```

5. **Write debug snapshot**:

   - Save `items` to `.stratdeck/last_position_monitoring.json` (mirroring `last_trade_ideas.json` pattern).

6. Output:
   - If `--json-output`:
     - Print `json.dumps(items, indent=2)` to stdout.
   - Else:
     - Render a simple table with:
       - `id`, `symbol`, `strategy_id`, `status`
       - `dte`, `unrealized_pl_total`, `% of max profit`
       - `ivr`
       - `action`, `reason`
     - For `IVR_BELOW_SOFT_EXIT` suggestions, highlight in some textual way (e.g., mark with `*`).

### 5.2 New Command: `positions close-auto`

**Goal:** Simulate closing any positions where exit rules recommend `action == "exit"` (based on latest metrics).

**CLI signature:**

```bash
python -m stratdeck.cli positions close-auto [--dry-run] [--json-output]
```

**Behaviour:**

1. Internally reuse the same logic as `positions monitor` to compute `metrics` and `decision` for each open position.
2. Filter positions where `decision.action == "exit"` and **status is still "open"**.
3. For each such position:
   - Optionally recompute `PositionMetrics` to ensure fresher data (OK to re-use if computed seconds ago).
   - Compute `exit_mid = metrics.current_mid`.
   - Compute `realized_pl_total`:
     - For credit spreads, consistent with entry PL logic:
       - `realized_pl_total = metrics.unrealized_pl_total`.
   - Update `PaperPosition`:
     - `status = "closed"`
     - `closed_at = now`
     - `exit_mid = exit_mid`
     - `realized_pl_total = realized_pl_total`
     - `exit_reason = decision.reason`
4. If `--dry-run`:
   - Do **not** persist updates; only show which positions **would** be closed and why.
5. Persist:
   - If not `--dry-run`, call `store.save()` (whatever the existing pattern is) to update `.stratdeck/positions.json`.
6. Output:
   - Summary list of closed positions (or would-be-closed positions in dry-run).
   - With `--json-output`, print a JSON array of `{position_before, position_after, metrics, decision}`.

### 5.3 New Command: `positions close --id`

**Goal:** Manually force-close a single position using current mid prices.

**CLI signature:**

```bash
python -m stratdeck.cli positions close --id <uuid> [--reason <str>] [--dry-run] [--json-output]
```

**Behaviour:**

1. Load the position by `id` from `PositionsStore`.
2. Compute `PositionMetrics` and `ExitDecision` (for reference).
3. If `--dry-run`:
   - Show what would happen (current mid, realized P&L, etc.).
4. Else:
   - Compute `exit_mid` and `realized_pl_total` as in `close-auto`.
   - Update fields:
     - `status = "closed"`
     - `closed_at = now`
     - `exit_mid`, `realized_pl_total`
     - `exit_reason`:
       - Use `--reason` if provided, else `decision.reason` or `"MANUAL"`.
   - Persist `PositionsStore`.
5. Output:
   - Small summary or JSON snapshot depending on flags.

---

## 6. Mapping Strategies to Families

Codex should implement a small helper to map `strategy_id` → `strategy_family` and `ExitRulesConfig` records.

Approach:

- Primary mapping from `exits.yaml` (`strategies` section).
- Fallback (if needed) via naming conventions:
  - `*_strangle_*` → `"short_strangle"`
  - `*_iron_condor_*` → `"iron_condor"`
  - `*_credit_spread_*` or `*_vertical_*` → `"credit_spread"`
  - `*_ratio_spread_*` → `"ratio_spread"`
  - `*_bwb_*` or `*_broken_wing_butterfly_*` → `"bwb"`
  - `*_diagonal_*` → `"diagonal"`

For Phase 3, we only need to guarantee full correctness for **strategies actually used** (`short_put_spread_index_45d` initially).

---

## 7. Testing & Acceptance Criteria

### 7.1 Unit Tests

Add tests in `tests/`:

1. **`test_position_metrics.py`**
   - Use synthetic `PaperPosition` instances with known leg structure.
   - Mock `market_data_client` and `vol_client` to return:
     - Underlying price.
     - Spread mids.
     - IV / IVR.
   - Assert:
     - `unrealized_pl_total` is computed correctly.
     - `max_profit_total` and `max_loss_total` are correct for:
       - Credit spread.
       - Iron condor (two spreads, treat per spread max or combined).
     - `pnl_pct_of_max_profit` matches expectation.
     - `dte` computed correctly.

2. **`test_exit_rules.py`**
   - Construct `PositionMetrics` for various scenarios:
     - 60% of max profit, DTE > 21 → action `exit`, reason `TARGET_PROFIT_HIT`.
     - 30% of max profit, DTE 15, `dte_exit=21` → action `exit`, reason `DTE_BELOW_THRESHOLD`.
     - 40% of max profit, DTE 30, `ivr=15`, `ivr_soft_exit_below=20`, `is_short_premium=true`:
       - Action might remain `"hold"` (per current design), reason `IVR_BELOW_SOFT_EXIT`, triggered_rules contains IVR message.
   - Ensure:
     - The order of evaluation enforces **profit target OR 21 DTE** properly.
     - IVR rule always adds a triggered rule when below threshold for short premium.

3. **`test_positions_cli_monitor.py`**
   - Use a temporary `.stratdeck/positions.json` fixture (tmpdir).
   - Insert a couple of open positions with known configs.
   - Mock market/vol data so metrics and decisions are deterministic.
   - Run `positions monitor --json-output` via CLI runner (if existing pattern) or directly call the function.
   - Assert:
     - Output JSON structure: list of items with `position`, `metrics`, `decision`.
     - `.stratdeck/last_position_monitoring.json` is written and contains same data.

4. **`test_positions_cli_close_auto.py`**
   - Similar fixture with multiple open positions.
   - Force one position to clearly hit profit target, another to DTE exit, another not triggered.
   - Run `positions close-auto --dry-run`:
     - Ensure no changes persisted.
     - Confirm correct list of would-be-closed positions in output.
   - Run `positions close-auto` (no `--dry-run`):
     - Ensure only triggered positions change to `status="closed"` and have `closed_at`, `exit_mid`, `realized_pl_total`, `exit_reason`.

5. **`test_positions_cli_close_single.py`**
   - Create one open position and run `positions close --id <id> --dry-run`:
     - Confirm metrics and planned P&L shown.
   - Then run without `--dry-run`:
     - Ensure position is closed and persisted.

### 7.2 Regression: Phase 1 & 2

- Ensure that:
  - `python -m stratdeck.cli trade-ideas ...` behaves exactly as before.
  - `python -m stratdeck.cli enter-auto` still:
    - Creates positions correctly.
    - Writes to `.stratdeck/positions.json`.
  - `python -m stratdeck.cli positions list` and `--json-output` behave unchanged.

No existing tests for Phases 1 and 2 should need to change except where they assert strict equality on `PaperPosition` models – in that case, adjust to ignore new optional fields or use subset comparisons.

---

## 8. Implementation Order (for Codex)

1. **Add models and helpers:**
   - `PositionMetrics`, `ExitRulesConfig`, `ExitDecision` in `stratdeck/tools/position_monitor.py`.
   - `load_exit_rules(strategy_id)` that loads `config/exits.yaml`.
   - `compute_position_metrics(...)` and `evaluate_exit_rules(...)`.

2. **Extend `PaperPosition` and `PositionsStore`:**
   - Add new optional fields.
   - Add `get_open_positions()` and `update_position()` helpers if not present.

3. **Implement `positions monitor`:**
   - Wire into `stratdeck/cli.py`.
   - Implement snapshot writing to `.stratdeck/last_position_monitoring.json`.

4. **Implement `positions close-auto`:**
   - Reuse monitoring flow.
   - Implement `--dry-run` and `--json-output`.

5. **Implement `positions close --id`:**
   - Manual single-position close.

6. **Add tests** as described above and ensure:
   - All new tests pass.
   - Existing tests remain green.

7. **Light documentation:**
   - Update `README` or `dev` docs (if exists) to describe:
     - New commands.
     - Exit rules config (`config/exits.yaml`).
     - The mechanical nature of exits: profit target OR 21 DTE, IVR<20 suggestions for short premium.

Once this is complete, Phase 3 will give you:

- A **daily runnable monitor** (`positions monitor`).
- A **paper exit engine** (`positions close-auto`) that mechanically enforces your tasty-style rules for short premium and other structures, driven by configuration, not hard-coded constants.
