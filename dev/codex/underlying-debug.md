In this repo (StratDeck Copilot), your job is to improve observability and robustness
of live underlying price hints.

CONTEXT:
- Read AGENTS.md and obey all rules.
- Live underlying prices are now resolved in stratdeck/agents/trade_planner.py via get_underlying_price(...).
- In live mode, this uses a data provider (TastyProvider) to fetch quotes and returns mid/last.
- When provider calls fail (e.g. HTTP 429), the helper returns None and the code falls back
  to TA/yfinance/synthetic prices.

GOAL:
- Make it obvious, in logs and behaviour, when:
  - A live quote is successfully used for underlying_price_hint.
  - A live quote fails and we fall back to TA/synthetic prices.
- Optionally improve robustness for SPX by:
  - If a direct SPX quote fails, try XSP as a fallback and use XSP * 10 as a hint.

REQUIREMENTS:
- Add structured logging in get_underlying_price, e.g. at DEBUG/INFO level:
  - When a live quote is used (symbol, mid/last).
  - When a live quote fails (symbol, error) and fallback is triggered.
- Implement an optional SPX fallback:
  - If fetching a live quote for "SPX" fails due to a provider error:
    - Attempt to fetch "XSP".
    - If successful, derive a synthetic SPX hint as 10 * XSP.mid (or last).
  - This fallback should only affect the underlying hint, not chains or pricing logic.
- Keep behaviour unchanged in mock mode.
- Add or update tests under tests/ to cover:
  - get_underlying_price using a mocked quote_fetcher and logging when live is used.
  - Fallback behaviour when SPX fetch raises an exception but XSP fetch returns a quote.
- Tests must not hit the real Tasty API; use mocks/monkeypatches.
- All tests must pass via 'python -m pytest'.

WORKFLOW:
1. Inspect get_underlying_price in stratdeck/agents/trade_planner.py.
2. Add logging statements and SPX->XSP fallback logic as described.
3. Update or add tests under tests/ for both success and fallback scenarios.
4. Run 'python -m pytest' and fix any failures.
5. Print a short summary of changes and 'git diff' at the end.
