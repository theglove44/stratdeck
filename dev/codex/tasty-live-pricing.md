In this repo (StratDeck Copilot), your job is to integrate **real-time option chains / quotes / greeks from Tastytrade**
into the existing pricing pipeline, behind a safe config switch.

CONTEXT:
- Read AGENTS.md and obey all rules (no live trading, tests must pass, CLI is a public API).
- StratDeck already has tools for pricing and chains under stratdeck/tools/:
  - chains.py / chain_pricing_adapter.py / pricing.py / greeks.py / vol.py
  - orders.py / positions.py / account.py may already reference Tasty or a mock.
- There is an environment variable STRATDECK_DATA_MODE which should control data source:
  - "mock" = use cached / synthetic data (safe default for tests).
  - "live" = use real market data from Tastytrade.

GOAL:
- When STRATDECK_DATA_MODE=live:
  - Use Tastytrade APIs / SDK to fetch:
    - Underlying quote (last, bid, ask, mark).
    - Option chain for a given symbol + expiry.
    - Greeks where available (delta, theta, vega, etc.) OR compute via greeks.py if not.
  - Plug this into the existing pricing pipeline so that:
    - TradeIdea.pop, credit_per_width, and estimated_credit are based on **real mid prices**.
    - Any greeks used by TraderAgent / risk logic come from real data or consistent local calcs.
- When STRATDECK_DATA_MODE=mock:
  - Preserve existing behaviour (mock/simulated data). Do NOT break current tests.

REQUIREMENTS:
- Do NOT place any live orders. You are only allowed to **read** data from Tastytrade.
- Reuse any existing Tastytrade client / session helpers in this repo (e.g. tools/account.py) if present.
- If no Tasty client exists, create a small adapter module (e.g. tools/tasty_client.py) that:
  - Handles login/session in one place.
  - Exposes simple functions such as:
    - get_underlying_quote(symbol)
    - get_option_chain(symbol, expiry)
- Keep the interface into pricing/chains clean; avoid leaking raw SDK objects everywhere.
- Add or update tests so that:
  - Tests run in STRATDECK_DATA_MODE=mock and DO NOT hit the network.
  - Tasty calls are **mocked or monkeypatched** in tests (e.g. fake chain/quote responses).
- All tests must pass (`python -m pytest`) on completion.

WORKFLOW:
1. Inspect stratdeck/tools/chains.py, chain_pricing_adapter.py, pricing.py, greeks.py and account.py:
   - Identify where chains and prices are currently coming from (e.g. yfinance, mock).
2. Introduce a clear abstraction for "data provider":
   - In code, branch on STRATDECK_DATA_MODE ("mock" vs "live").
   - Implement a "live" provider that uses Tastytrade to fetch chains/quotes.
3. Ensure pricing logic uses **mid price** ( (bid + ask) / 2 ) consistently when in live mode.
4. Thread real quotes/greeks into the existing POP / credit_per_width calculations.
5. Add or update tests:
   - Keep them in mock mode.
   - Monkeypatch the data provider so behaviour is deterministic.
6. Run `python -m pytest` and fix any failures.
7. At the end, print:
   - A short bullet summary of changes.
   - The output of `git diff` for inspection.

