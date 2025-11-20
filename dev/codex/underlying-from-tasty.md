In this repo (StratDeck Copilot), your job is to ensure that when STRATDECK_DATA_MODE=live,
the 'underlying_price_hint' used in trade ideas comes from the TastyProvider quotes,
not from yfinance/synthetic data.

CONTEXT:
- Read AGENTS.md and obey all rules.
- We already have TastyProvider in stratdeck/data/tasty_provider.py with get_quote(symbol)
  that returns 'bid', 'ask', 'last', 'mid', etc.
- Trade ideas are generated via stratdeck/agents/trade_planner.py and surfaced by:
  python -m stratdeck.cli trade-ideas --strategy ... --universe ... --json-output
- Currently 'underlying_price_hint' is not aligned with Tasty quotes (e.g. XSP quote ~664.22
  while SPX underlying_price_hint ~6722.11).

GOAL:
- When STRATDECK_DATA_MODE=live:
  - Use TastyProvider.get_quote(trade_symbol) or data_symbol as appropriate
    and set underlying_price_hint = mid if available, else last.
- When STRATDECK_DATA_MODE=mock (or unset):
  - Preserve existing behaviour (yfinance / synthetic data via Chartist/TA).

REQUIREMENTS:
- Introduce a single, well-defined function or helper to obtain the underlying price, e.g.:
  get_underlying_price(symbol: str, data_mode: str) -> float
- For index symbols (SPX, XSP, etc.), ensure you use the correct mapping between:
  - trade_symbol
  - data_symbol
  but avoid any arbitrary 10x multipliers; rely on real quotes.
- Wire this helper into wherever 'underlying_price_hint' is currently computed.
- Add or update tests to:
  - Assert that in mock mode, behaviour is unchanged.
  - Assert that in a 'live' test scenario (with TastyProvider/get_quote monkeypatched),
    underlying_price_hint uses the mocked mid/last value.
- Tests must not hit the real Tasty API (mock the provider).
- All tests must pass via 'python -m pytest' when you are done.

WORKFLOW:
1. Find where 'underlying_price_hint' is set in the trade idea planner / strategy engine.
2. Refactor that code to call a new helper that branches on STRATDECK_DATA_MODE.
3. Implement the 'live' branch using TastyProvider/get_quote, injected or imported cleanly.
4. Add or adjust tests under tests/ to cover both mock and live modes (with mocks).
5. Run 'python -m pytest' and fix any failures.
6. Print a short summary of changes and 'git diff' at the end.
