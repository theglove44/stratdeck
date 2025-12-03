# Codex-Max Task Spec — StratDeck Human Rules Strategy Engine

## 0. Meta

- **Repository**: `git@github.com:theglove44/stratdeck.git`
- **Project**: StratDeck Agent System — options strategy scanner & lifecycle engine
- **Primary user**: Chris
- **Runtime**: Python, Pydantic v2, CLI via `python -m stratdeck.cli ...`
- **Goal**: Make the strategy engine behave exactly like Chris’s *documented human rules* for
  - Short Put Spreads (index + equity)
  - Iron Condors (index)
- **Mode**: Full autonomy inside this repo with:
  - Discovery (search files, understand current architecture)
  - Code editing (YAML + Python)
  - End-to-end tests
  - Self-checking and self-correction loops when anything fails

---

## 1. Source of Truth

### 1.1 Human Rules Document

Treat `options_strategies.md` (human rules) as **the source of truth** for:

- Entry criteria  
- Market regime filters  
- Risk limits  
- Exit rules  

You must align code and config to match:

**Short Put Spreads (bullish)**

- **Product**: Index (SPX, XSP, etc.) and equities from specific universes
- **Direction**: Bullish
- **Expiry**:
  - Use **monthly options only** (no weeklies)
  - Target **45 DTE**, acceptable window **45 ± 5** days
- **Delta**:
  - Short leg at/near **30Δ**
  - Acceptable band: **25–35Δ**
- **Width**:
  - **Max $5 wide**
  - Prefer / enforce fixed width of **$5**
- **Credit / width**:
  - Target ≈ **⅓ credit per $1 width**
  - Minimum acceptable ≈ **0.25 credit per $1 width**
- **POP**:
  - **≥ 60%**
- **IV Rank**:
  - **≥ 25%**
- **Trend / regime**:
  - Only in **uptrend** (or pullback in uptrend)
  - No new trades in clear downtrends
- **Risk**:
  - **Max buying power ≈ $500** per trade
  - Prefer **1 position per symbol**
- **Earnings (equities)**:
  - No entry if earnings are within **21 days before expiry**

**Call Credit Spreads (bearish)**

- Same structure, but:
  - Direction: **bearish**
  - Short call ≈ **–30Δ**
  - Trend must be **downtrend / bearish**

**Iron Condors (neutral)**

- **Direction**: Neutral / range-bound
- **Expiry**: same 45 ± 5 DTE, monthlies only
- **Delta wings**:
  - Short put and short call ≈ ±30Δ (within ±5Δ band)
- **Width**: fixed **$5**
- **IVR**:
  - Prefer **IVR ≥ 30%**
- **Trend / regime**:
  - Only when **range-bound / choppy / consolidation**
  - Block clear bullish or bearish trends
- **Delta neutrality**:
  - Net position delta must be **near 0**
  - Example rule: `abs(net_delta) ≤ 2.0` at entry
- **Risk limits & earnings**:
  - Same BP and earnings rules as above where applicable

When in doubt, defer to the wording and intent of the human rules document.

---

## 2. Current State to Inspect

Before changing anything, you must **discover and understand** the current implementation:

1. **Config**:
   - `stratdeck/config/strategies.yaml`
2. **Strategy / candidate engine** (names may differ; you must find them):
   - CLI entry: `stratdeck/cli/trade_ideas.py` or similar
   - Candidate builder: e.g. `stratdeck/trade_ideas/builder.py`, `strategies/index_spreads.py`, etc.
   - Filter engine: e.g. `stratdeck/filters/*.py`
3. **Position & regime**:
   - Trend/vol regime detection utilities (likely under `stratdeck/analysis/` or `stratdeck/data/`)
   - Any earnings lookup helpers
4. **Tests**:
   - Existing tests for strategies, filters and trade-ideas CLI under `tests/`

You must use repository search (e.g. ripgrep) to identify:

- Where `width_rule`, `min_pop`, `min_ivr` are consumed
- Where DTE and delta are computed and used
- Where trend_regime and vol_regime are attached to candidates

---

## 3. Required Changes (Functional Objectives)

### 3.1 Strategy Templates (YAML) — Align With Human Rules

Update `stratdeck/config/strategies.yaml`:

1. Replace or augment:
   - `short_put_spread_index_45d`
   - `short_put_spread_equity_45d`
   - `iron_condor_index_30d`
2. Introduce explicit configuration keys to support human rules:

   - `expiry_rules`:
     - `monthlies_only: true`
     - `earnings_buffer_days: 21` (equity use)
   - Tightened `dte`:
     - `target: 45`
     - `min: 40`
     - `max: 50`
   - `delta.short_leg`:
     - `target: 0.30`
     - `min: 0.25`
     - `max: 0.35`
   - `width_rule`:
     - For these strategies, use a **fixed width of 5**
   - `risk_limits`:
     - `max_buying_power: 500`
     - `max_positions_per_symbol: 1`
   - `filters`:
     - `min_pop: 0.60`
     - `min_credit_per_width: 0.25`
     - `min_ivr: 0.25` (0.30+ for ICs)
     - `allowed_trend_regimes` and `blocked_trend_regimes` per strategy type
     - For ICs: `max_position_delta: 2.0`

3. Ensure YAML remains valid and consistent with how existing code reads it.

---

### 3.2 Candidate Generation — Enforce Human Rules at Construction

Modify the candidate generation logic so that **most rules are obeyed by construction**, not only by filters:

1. **Expiry selection**:
   - Filter option chains to:
     - Only **monthly expirations** when `expiry_rules.monthlies_only` is true.
     - `dte` within `[dte.min, dte.max]` configured in YAML.
2. **Delta-first leg selection**:
   - For spreads:
     - Choose the short leg with delta closest to `delta.short_leg.target`
       while staying within `[min, max]`.
   - For ICs:
     - Select put and call shorts with ≈ ±30Δ according to the same band.
3. **Width constraint**:
   - For these human-rule strategies, build only spreads/condors with width = 5.
4. **Equity earnings filter at generation time**:
   - If `expiry_rules.earnings_buffer_days` is set and the underlying has an earnings date:
     - Skip expiries/structures where earnings are within that buffer window before expiry.

Candidate objects must carry computed fields used by filters:

- `dte`, `short_leg.delta`, `width`, `credit_per_width`, `pop`, `ivr`, `buying_power`
- `trend_regime`, `vol_regime`
- `earnings_date` (or `None`)
- For ICs, `position_delta` or equivalent

---

### 3.3 HumanRulesFilter — Make Chris’s Logic a First-Class Filter

Implement a new filter class, e.g.:

- File: `stratdeck/filters/human_rules.py`
- Class: `HumanRulesFilter`

Responsibilities:

- Consume strategy config from YAML (e.g. `strategy_cfg.filters`, `risk_limits`, `expiry_rules`, `dte`, `delta`, `width_rule`)
- Inspect a **single candidate** and:
  - Return an **empty list** if it passes all human rules.
  - Return a **list of human-readable rejection reasons** otherwise.

Filter must cover at least:

1. **DTE window**:
   - Reject if `dte` falls outside `[dte.min, dte.max]`.
2. **Expiry type**:
   - If `monthlies_only` and candidate uses a weekly or non-monthly expiry → reject.
3. **Delta rules**:
   - Reject if short-leg delta is outside `[delta.short_leg.min, delta.short_leg.max]`.
4. **Width**:
   - Reject if `width` > configured width (5) or not equal to it if fixed.
5. **Credit / width**:
   - Reject if `credit_per_width < min_credit_per_width`.
6. **POP**:
   - Reject if `pop < min_pop`.
7. **IV Rank**:
   - Reject if `ivr < min_ivr` or `ivr > max_ivr` where configured.
8. **Trend regime**:
   - If `allowed_trend_regimes` is configured:
     - Reject if candidate’s `trend_regime` isn’t in allowed list.
   - If `blocked_trend_regimes` is configured:
     - Reject if candidate’s `trend_regime` is in blocked list.
9. **Buying power & positions**:
   - Reject if `buying_power > risk_limits.max_buying_power`.
   - If there’s a way to check **existing open positions** per symbol and strategy:
     - Reject if `max_positions_per_symbol` would be exceeded.
10. **Earnings buffer**:
    - For equities, if `earnings_date` exists and the difference between entry date and earnings is less than `earnings_buffer_days` → reject.
11. **Iron Condor net delta**:
    - For ICs, reject if `abs(position_delta) > max_position_delta`.

Integration:

- Register `HumanRulesFilter` into the existing filter pipeline for trade-ideas.
- Ensure **only one filter needs to reject**:
  - On first non-empty rejection reason list, stop and report the reason(s).

Error messaging:

- Each rejection must be phrased clearly, e.g.:

  - `DTE 39 outside allowed range [40, 50]`
  - `Short leg delta 0.21 outside [0.25, 0.35]`
  - `Weekly expiry not allowed (monthlies_only = true)`
  - `Credit/width 0.18 < minimum 0.25`
  - `IV Rank 0.18 < minimum 0.25`
  - `Trend regime downtrend not in allowed ['bullish', 'pullback_in_bullish']`

---

### 3.4 Exit Rules — Position Monitor Hooks (Skeleton)

If the repo already has a position monitoring / exit rules module, extend it; otherwise, add a new module with a minimal structure:

- Configurable exit rules per strategy, e.g.:

  ```yaml
  exit_rules:
    profit_target_fraction: 0.5      # 50% of credit
    dte_exit_target: 21
    dte_exit_flex: 5
    respect_earnings: true
  ```

- A function like:

  ```python
  def check_exit_signals(position, rules, now) -> list[str]:
      # Check profit target:
      # Check DTE window around 21:
      # Check earnings proximity:
      # Return list of recommended exit reasons
  ```

At this stage it’s acceptable to:

- Implement the exit rules module and wire it into any existing monitoring pipeline.
- Add tests for the exit logic even if the surrounding orchestration is minimal.

---

## 4. End-to-End Behaviour Expectations

After your changes, the system must:

1. **Generate candidates** that already obey:
   - DTE ≈ 45 ± 5 (monthlies only)
   - Delta ≈ ±30Δ within tolerance
   - Width = 5
2. **Filter candidates** using HumanRulesFilter and others such that:
   - Anything violating Chris’s human rules is rejected with clear reasons.
   - Trade ideas that survive are consistent with what Chris would pick manually.
3. **Allow CLI usage** like:

   ```bash
   export STRATDECK_DATA_MODE=live

   python -m stratdeck.cli trade-ideas      --universe index_core      --strategy short_put_spread_index_45d      --json-output
   ```

   - Output should show:
     - For each candidate: symbol, strategy_id, spread_width, dte_target, ivr, pop, credit_per_width.
     - No trades that obviously violate the human rules (e.g. 10-wide spreads, <60% POP, IVR < 25%, weekly expiries).

4. **Exit rules**:
   - For any tracked position using these strategies, `check_exit_signals` should:
     - Emit a signal when 50% profit is captured.
     - Emit a signal when DTE is in the 21 ± flex window.
     - Optionally emit a signal when earnings are approaching.

---

## 5. Self-Checking & Self-Correction Loop

You must implement a **self-check workflow** and **auto-correct** any issues you introduce.

### 5.1 Test & Check Commands

You must run, in this order:

1. **Unit tests**:

   ```bash
   pytest -q
   ```

2. **Trade-ideas smoke tests** (non-interactive):

   ```bash
   export STRATDECK_DATA_MODE=live

   python -m stratdeck.cli trade-ideas      --universe index_core      --strategy short_put_spread_index_45d      --json-output > /tmp/ideas_index_put.json

   python -m stratdeck.cli trade-ideas      --universe tasty_watchlist_chris_historical_trades      --strategy short_put_spread_equity_45d      --json-output > /tmp/ideas_equity_put.json
   ```

   If Iron Condor support is fully wired:

   ```bash
   python -m stratdeck.cli trade-ideas      --universe index_core      --strategy iron_condor_index_45d      --json-output > /tmp/ideas_ic_index.json
   ```

3. **Sanity checks on JSON outputs**:

   - Verify:
     - `dte` values fall in [40, 50].
     - `width` is 5 for these strategies.
     - `pop >= 0.60`, `ivr >= configured min_ivr`.
   - If there are **no candidates**:
     - Confirm it’s due to tight filters (maybe market conditions) and not a bug.
     - Relax filters slightly or adjust logic as needed while staying aligned with the human rules doc.

### 5.2 Failure Handling

For **any** failure (tests or runtime):

1. Capture:
   - Full stack trace
   - Failing command
   - Relevant file paths and line numbers
2. Diagnose:
   - Open the failing file(s)
   - Identify incorrect assumptions (e.g. missing config keys, mis-named fields, wrong data types)
3. Correct:
   - Adjust YAML, Python types, or logic
   - Maintain backwards compatibility where reasonable
4. Re-run:
   - Re-run the **exact same command** that failed.
   - Continue until either:
     - The failure is resolved, or
     - You have made **3 reasonable attempts** and can explain precisely what still blocks you.

If a test repeatedly fails due to missing or ambiguous behaviour in the existing codebase, you must:

- Document the ambiguity clearly.
- Suggest a sensible default.
- Implement the default while keeping the code easy to refactor later.

### 5.3 Self-Consistency Checks

After all tests pass:

1. **Cross-check YAML vs Code**:
   - Ensure every new YAML key is actually read in code.
   - Remove any dead or unused config you introduced, or clearly mark it for future use.
2. **Cross-check Code vs Human Rules**:
   - Walk through each bullet point of the human rules for:
     - Short put spreads
     - Call credit spreads
     - Iron condors
   - For each bullet, identify:
     - Where it is implemented (file + function + line range).
     - Or note if it is not yet implemented; if not, implement it unless impossible.
3. **Log / provenance**:
   - Confirm that rejection reasons from `HumanRulesFilter` are:
     - Propagated into trade-ideas output or logs.
     - Understandable to a human (Chris) reading them.

---

## 6. Code Style & Constraints

- Follow existing project style (imports, function naming, type hints).
- Respect Pydantic v2 usage patterns where relevant.
- Write **focused, small functions** rather than massive monoliths.
- Add or update tests when:
  - You add new logic.
  - You change behaviour that was previously in tests.
- Prefer explicitness over cleverness when encoding risk/entry rules.

---

## 7. Deliverables Checklist

You are done when:

- [ ] `stratdeck/config/strategies.yaml` aligns with human rules and loads without error.
- [ ] Candidate builder enforces:
  - monthlies only
  - 45 ± 5 DTE
  - 5-wide spreads
  - delta selection around 30Δ
  - equity earnings buffer
- [ ] `HumanRulesFilter` exists and is wired into the filter pipeline.
- [ ] Filters reject violations with clear messages.
- [ ] Exit rules module exists with a working `check_exit_signals` function.
- [ ] `pytest -q` passes.
- [ ] Smoke tests for trade-ideas run without error.
- [ ] Example JSON output from trade-ideas shows only trades consistent with Chris’s human rules.
- [ ] You have performed the self-consistency checks and corrected any mismatches.

If any box cannot be ticked, you must clearly explain why and propose a path to fix it in a future task.
