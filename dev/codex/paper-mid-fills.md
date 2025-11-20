CONTEXT:
- Read AGENTS.md and obey all rules (no live trading).
- Relevant modules (names may vary, inspect them first):
  - stratdeck/tools/orders.py       # order preview / placement
  - stratdeck/tools/positions.py    # position storage (CSV/DB)
  - stratdeck/tools/pricing.py
  - stratdeck/tools/chain_pricing_adapter.py
- Trade ideas are represented by TradeIdea in stratdeck/agents/trade_planner.py.
  TradeIdea already carries:
  - legs (short/long, type, strike, expiry, quantity)
  - pop, credit_per_width, estimated_credit, provenance, etc.
- Live prices and chains are available via the data provider and Tasty integration
  controlled by STRATDECK_DATA_MODE=live.

GOAL:
- When the user (or a future orchestrator) chooses to "enter" a trade idea:
  - Price the legs using **mid price** from the pricing layer.
  - Compute total credit/debit and per-width metrics as needed.
  - Log the trade as a **paper position** in the local positions store.
- Do NOT send live orders to Tastytrade in this task.

REQUIREMENTS:
- Respect any existing environment flags that indicate "paper" vs "live" trading.
  - If none exist, introduce STRATDECK_TRADING_MODE with:
    - "paper" (default)
    - "live" (for future use, but DO NOT implement live order placement here).
- Implement or refine a single code path such as:
  - enter_paper_trade(trade_idea, account_id=None, data_mode="live"/"mock")
  in orders.py (or equivalent), which:
  - Uses mid pricing for all legs (via pricing/chain_pricing_adapter).
  - Does NOT call any live order placement APIs.
  - Returns a clear summary (symbol, legs, mid fill price, total credit/debit, DTE, etc.).
- Ensure positions.py (or equivalent) logs at least:
  - symbol / underlying
  - strategy / direction
  - quantity
  - entry_mid_price and/or entry_credit
  - DTE / expiry
  - a snapshot of provenance (e.g. strategy_template_name, universe_name, filters_applied).
- Positions should be stored in the existing store this repo already uses
  (CSV/DB under stratdeck/tools/positions.py) and not in a new ad-hoc location.

TESTING REQUIREMENTS:
- Add or update tests under tests/ so that:
  - Placing a paper trade using a fake TradeIdea and mocked pricing:
    - Calls into the paper entry function.
    - Produces a new record in the positions store with expected fields.
    - Uses mid price from the pricing layer (can be mocked).
  - Tests DO NOT hit the network or call real Tastytrade APIs.
- Tests must pass via: python -m pytest

WORKFLOW:
1. Inspect stratdeck/tools/orders.py and stratdeck/tools/positions.py to understand:
   - Current preview/place behaviour.
   - How and where positions are stored.
2. Define or confirm a single "enter paper trade" path using TradeIdea as input.
3. Ensure this path:
   - Uses mid pricing for each leg (from existing pricing utilities).
   - Treats all fills as paper-only, with no live order placement.
   - Writes a new position record with the required fields, including provenance.
4. Add or update tests to cover:
   - A happy-path paper entry with mocked pricing and a simple TradeIdea.
   - Verification that positions storage contains the expected new record.
5. Run 'python -m pytest' and fix any failures.
6. At the end, print:
   - A short bullet summary of your changes.
   - The output of 'git diff' for inspection.