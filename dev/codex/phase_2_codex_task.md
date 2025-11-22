# PROJECT: StratDeck Agent System – Phase 2 Paper Trading Engine (Entry + Storage)

## Overview

You are working in the `stratdeck-copilot` repo. Phase 1 of the trade-ideas pipeline is complete (stable hints, provenance, filters, last ideas file). Phase 2’s goal is to build the **paper trading engine** for entries and basic position storage.

When this task is complete, Chris should be able to:

1. Run `trade-ideas` to generate candidates (as in Phase 1).
2. Run a simple CLI (`enter-auto`) that:
   - Selects a trade idea (initially: the “top”/first idea).
   - Fetches fresh mid-prices for all legs.
   - Calculates basic entry metrics (credit/debit, max profit/risk if feasible).
   - Persists a **paper position** record to disk.
3. Run a positions CLI to list current paper positions with key info.

Future phases (monitoring/exits/orchestrator) will build on this, so treat this as a solid foundation for all **paper-mode lifecycle** tracking.

---

## Context / Current State (what you should assume)

- Repo: `stratdeck-copilot` (Python 3.9, Pydantic v2).
- Core CLI entry point: `python -m stratdeck.cli ...`.

Phase 1 has delivered:

- `python -m stratdeck.cli trade-ideas --universe index_core --strategy short_put_spread_index_45d --json-output`
  - Produces a JSON array of trade ideas to stdout.
  - Also writes `.stratdeck/last_trade_ideas.json` with the same array.
- Each trade idea includes at least:
  - `symbol`, `data_symbol`, `trade_symbol`
  - `strategy` (shape name, e.g. `short_put_spread`)
  - `strategy_id` (e.g. `short_put_spread_index_45d`)
  - `universe_id` (e.g. `index_core`)
  - `direction` (e.g. `bullish`)
  - `legs` (one or more option legs; includes strikes, expirations, type, side, etc.)
  - `underlying_price_hint`
  - `dte_target`, `spread_width`, `target_delta`
  - `pop`, `credit_per_width`, `estimated_credit`
  - `filters_passed`, `filters_applied`, `filter_reasons`

- The “live data” side (chains/quotes) is accessed via:
  - `stratdeck/tools/chain_pricing_adapter.py`
  - `stratdeck/tools/chains.py`
  - (Do **not** rename these modules.)

Phase 2 must **not** break any of the above. You are adding new capabilities on top.

---

## High-Level Objectives

1. Introduce a `PaperPosition` data model that can represent an opened paper options strategy based on a trade idea.
2. Implement a `PositionsStore` abstraction that persists positions to disk using a simple, robust format (JSON file).
3. Add a CLI command (e.g. `enter-auto`) to convert a chosen trade idea into a `PaperPosition` using fresh mid-prices.
4. Add a CLI command (e.g. `positions list`) to inspect open paper positions in a human-friendly way (and JSON if needed).
5. Cover the new behaviour with tests so this is a stable base for monitoring/exits work in Phase 3.

---

## Scope of This Task

**In-scope**

- New data models for paper positions.
- File-backed positions store (JSON file under `.stratdeck/`).
- CLI commands:
  - `enter-auto` (or similar) to create new paper positions from last trade ideas.
  - `positions list` (and optionally `positions show`) to view stored positions.
- Integration with chain/pricing adapters to get fresh mid-prices on entry.
- Unit + small integration tests.

**Out-of-scope**

- Any real-money order placement or broker integration.
- Automated monitoring, exits, or orchestrator loops (that’s Phase 3).
- Complex P&L history tracking – keep it to basic entry metrics for now.
- Databases (Postgres, SQLite, etc.) – Phase 2 is pure JSON file persistence.

---

## Workstream 1 – PaperPosition Model & PositionsStore

### Goals

- Create a robust yet simple model for paper positions, capable of representing multi-leg strategies (spreads, iron condors, etc.).
- Implement a file-backed `PositionsStore` that can:
  - Load existing positions from disk.
  - Append new positions.
  - Return lists of positions filtered by status (open/closed).

### Requirements

1. **Module & structure**

   - Add a new module for positions, for example:

     - `stratdeck/tools/positions.py`

   - This module should define:
     - `PaperPosition` model (Pydantic or dataclass – prefer Pydantic if consistent with rest of code).
     - Any helper models (e.g. `PaperPositionLeg`) if needed.
     - `PositionsStore` abstraction for persistence.

2. **PaperPosition fields**

   The model must be able to represent:

   - Identity / linkage:
     - `id`: string UUID (e.g. `str(uuid4())`).
     - `symbol`: underlying symbol (e.g. `SPX`, `XSP`).
     - `trade_symbol`: symbol used for trading if different from `symbol`.
     - `strategy_id`: from trade idea (e.g. `short_put_spread_index_45d`).
     - `universe_id`: from trade idea (e.g. `index_core`).
   - Strategy / structure:
     - `direction`: `bullish` / `bearish` / `neutral` (copied from trade idea).
     - `legs`: a list of legs. You can:
       - Either define a dedicated leg model (e.g. `PaperPositionLeg`) with fields like `option_type`, `strike`, `expiration`, `side`, `quantity`, entry price, etc.
       - Or reuse/transform whatever leg structure is used in the trade ideas to ensure we can reconstruct what was traded.
   - Quantities & pricing:
     - `qty`: integer (number of strategy units, e.g. 1 spread = `qty=1`).
     - `entry_mid`: the mid-price at entry **per strategy unit** (sum of legs, as a net credit or debit).
     - `entry_credit` / `entry_debit`: net entry for the requested quantity (can be represented as a single signed field if smaller).
   - Risk / P&L envelope (Phase 2: keep it basic):
     - It’s acceptable to store simple derived metrics if easy to compute (e.g. `max_profit`, `max_loss`) but this is **optional** for Phase 2. Do not overcomplicate it.
   - Lifecycle:
     - `status`: string enum: `"open"` or `"closed"` (phase 2 will only ever create `"open"`).
     - `opened_at`: datetime (UTC, ISO-8601 serialised).
     - `closed_at`: optional datetime (for future use).
     - `exit_reason`: optional string (Phase 3+).

   Favour a clear, minimal model that won’t paint us into a corner.

3. **PositionsStore**

   Implement a simple store class that persists positions to a JSON file, for example:

   - Default path: `.stratdeck/positions.json` in the current working directory.

   Behaviour:

   - On initialisation, it should attempt to load an existing file if it exists.
     - If the file is missing, start with an empty list.
   - Provide methods such as:
     - `list_positions(status: str | None = None) -> list[PaperPosition]`
       - `status=None` returns all.
       - `status="open"` returns only open positions, etc.
     - `add_position(position: PaperPosition) -> None`
       - Appends and saves to disk.
   - When writing:
     - Ensure atomic-ish behaviour:
       - Acceptable: write to a temp file and then rename over the original.
       - At minimum: avoid leaving a truncated file on errors.
   - If the `.stratdeck` directory does not exist, create it.

4. **JSON format**

   - Store positions as a JSON array of objects, each corresponding to one `PaperPosition`.
   - The format should be stable and straightforward to inspect manually (no weird custom encoding).

### Tests / Acceptance Criteria

- Add unit tests for `PositionsStore`:
  - Using a temporary directory as CWD, ensure:
    - New store with no existing file starts empty.
    - `add_position` writes a file.
    - Re-initialising the store reads back the same positions.
    - `list_positions(status="open")` filters correctly when some positions are marked `"closed"` (you can simulate closure manually in the test).

- Tests should not rely on any external services (no live data). Use simple dummy `PaperPosition` instances.

---

## Workstream 2 – enter-auto: Convert TradeIdea → PaperPosition

### Goals

- Provide a one-shot CLI command to enter a paper position from the most recent trade ideas.
- Use **fresh** pricing for legs (mid-prices) at entry time, not stale estimates from the initial scan.

### Requirements

1. **CLI design**

   - Add a new subcommand to `stratdeck.cli`, for example:

     ```bash
     python -m stratdeck.cli enter-auto --qty 1 --confirm
     ```

   Basic behaviour:

   - Reads the last trade ideas from `.stratdeck/last_trade_ideas.json`.
   - Selects a single candidate idea to enter.
     - Phase 2 can use a simple rule: pick the **first** idea in the array (index 0).
     - Leave TODO comments where you’d later implement ranking/selection.
   - Uses the chosen idea + fresh pricing to build and store a `PaperPosition` via `PositionsStore`.
   - Prints a human-readable summary of the entered position (symbol, strategy, qty, entry price, expected credit, etc.).

   Flags / options:

   - `--qty N` (required or default `1`): quantity of the strategy to enter.
   - `--index N` (optional): index into the ideas array to pick (default 0).
   - `--json-output` (optional): print the created `PaperPosition` as JSON to stdout instead of just human text.
   - `--confirm`:
     - If present, proceed without interactive prompt.
     - If absent, show a summary of what will be entered and require a `y/N` confirm in interactive mode (for now you can keep this simple).

2. **Reading last trade ideas**

   - Implement a helper to load `.stratdeck/last_trade_ideas.json`:
     - Validate that it exists and contains a non-empty array.
     - If the file is missing or empty, exit with a clear error message.
   - Convert the chosen idea JSON back into the appropriate Pydantic model or dict structure for use in the planner/pricing logic.

3. **Fresh mid-pricing for legs**

   - Use existing chain/pricing logic (`chain_pricing_adapter`, etc.) to obtain up-to-date mid-prices for all legs of the chosen idea.
   - Compute:
     - Net mid-price **per strategy unit** (`entry_mid`), as a signed credit/debit.
     - Net entry amount for the requested quantity (e.g. `entry_mid * qty * contract_multiplier` if applicable; respect whatever conventions the repo uses, such as cents vs dollars and 100x multiplier for options).

   - Handle errors gracefully:
     - If you cannot fetch prices for the legs (e.g. Tastytrade error that is not recoverable), exit with a clear message and **do not** write a position.

4. **Create and store PaperPosition**

   - Build a `PaperPosition` instance from:
     - Trade idea fields: `symbol`, `trade_symbol`, `strategy_id`, `universe_id`, `direction`, `legs`, etc.
     - Pricing fields: `entry_mid`, `entry_credit`/`entry_debit`, etc.
     - Lifecycle fields: `id` (UUID), `status="open"`, `opened_at` (current UTC time).
     - `qty` from CLI flag.

   - Use `PositionsStore.add_position(...)` to persist.

5. **Output**

   - Human-readable default output, e.g.:

     ```text
     [enter-auto] Entered paper position:
       id: <uuid>
       symbol: XSP
       strategy: short_put_spread_index_45d
       qty: 1
       entry_mid: 1.50 credit
       entry_notional: $150.00
     ```

   - If `--json-output` is provided, print the `PaperPosition` JSON only (machine-readable).

### Tests / Acceptance Criteria

- Add an integration-style test that:
  - Uses a temporary working directory.
  - Creates a fake `.stratdeck/last_trade_ideas.json` containing one simple trade idea.
  - Mocks/stubs pricing so that “fresh mid” returns a known constant.
  - Runs `enter-auto` (via the CLI harness or function-level equivalent).
  - Asserts that:
    - `.stratdeck/positions.json` is created.
    - It contains exactly one `PaperPosition` with:
      - Correct `symbol`, `strategy_id`, `universe_id`, `direction`.
      - Correct `qty` and `entry_mid` as per mocked pricing.
      - `status == "open"` and `opened_at` set.

- Add a failure-path test:
  - If `.stratdeck/last_trade_ideas.json` is missing or empty:
    - `enter-auto` should exit with a non-zero status (or raise a well-defined exception) and print a meaningful error message.
  - Ensure no positions file is created in this case.

- Tests should avoid actual Tastytrade network calls:
  - Use dependency injection or monkeypatching to stub pricing where needed.

---

## Workstream 3 – Positions Listing & Basic Reporting

### Goals

- Provide a simple way to inspect current paper positions from the CLI.
- Expose both human-friendly output and machine-readable JSON where useful.

### Requirements

1. **CLI: positions list**

   - Add a `positions` subcommand with at least a `list` action, e.g.:

     ```bash
     python -m stratdeck.cli positions list
     ```

   Behaviour:

   - Loads positions via `PositionsStore`.
   - By default, shows **open** positions only.
   - Provide flags:
     - `--all`: include closed positions too (once those exist in later phases).
     - `--json-output`: emit JSON array of positions instead of table text.

   Human-readable output can be a simple table, for example:

   ```text
   ID                                   Symbol  Strategy                      Qty  Status  Entry mid
   -----------------------------------  ------  ----------------------------  ---  ------  ---------
   5e8d2c5c-...-a3f7                    XSP     short_put_spread_index_45d    1    open    1.50 cr
   ```

   Keep formatting simple and robust.

2. **(Optional) CLI: positions show**

   - You may add:

     ```bash
     python -m stratdeck.cli positions show --id <uuid>
     ```

   - Which prints detailed information for a single position.
   - This is optional but a nice-quality-of-life addition if it’s not too much work.

3. **JSON output contract**

   - When `--json-output` is provided:
     - Output a JSON array (for `list`) or single object (for `show`) corresponding to serialised `PaperPosition` models.
   - The JSON schema should match the Pydantic model fields defined earlier.

### Tests / Acceptance Criteria

- Add tests for `positions list`:
  - With a temp directory:
    - Pre-create a small positions file with 1–2 open positions.
    - Run `positions list` and assert:
      - It exits successfully.
      - In `--json-output` mode, the JSON array matches the pre-created file contents (modulo ordering).
  - Optionally check human-readable output contains expected substrings (e.g. symbols, strategy_ids).

- Ensure that if no positions file exists, `positions list`:
  - Prints an informative “no positions” message.
  - Exits successfully (it should not be an error to have no positions).

---

## Non-Functional Constraints

- Do **not** break existing commands or behaviour from Phase 1.
- Keep dependencies internal; do not add external packages.
- Avoid coupling business logic too tightly to CLI parsing; core functions should be reusable from future orchestrator/agent layers.

---

## What Not to Change

- Do **not** change:
  - Existing CLI flags or semantics for `trade-ideas`.
  - Names/paths of major modules used in Phase 1 (e.g. `trade_planner.py`, `strategies.py`, `strategy_engine.py`, `chain_pricing_adapter.py`).

- Do **not** introduce real broker order placement or anything that touches live accounts.

---

## How to Run & Validate (for you / for Chris)

After implementing this spec, from the repo root:

```bash
# 1) Run tests
pytest -q
```

```bash
# 2) Generate trade ideas (Phase 1 behaviour)
export STRATDECK_DATA_MODE=live

python -m stratdeck.cli trade-ideas   --universe index_core   --strategy short_put_spread_index_45d   --json-output > /tmp/ideas.json
```

```bash
# 3) Enter a paper position
python -m stratdeck.cli enter-auto --qty 1 --confirm
```

- Confirm that:
  - `.stratdeck/positions.json` exists.
  - It contains at least one open position with the expected fields.

```bash
# 4) List positions
python -m stratdeck.cli positions list

# Optional JSON listing
python -m stratdeck.cli positions list --json-output > /tmp/positions.json
jq '.[0] | {id, symbol, strategy_id, universe_id, qty, status, entry_mid}' /tmp/positions.json
```

When all acceptance criteria above are satisfied and tests are green, Phase 2 (Paper Trading Engine – entry + storage) is considered **complete**.
