# PROJECT: StratDeck Copilot – Underlying Price Helper (Streaming-First)

You are working on the **Planner / Strategy Engine** side of StratDeck, specifically how we resolve the `underlying_price_hint` used in trade ideas.

The goal of this task is to centralise underlying price selection into a single helper that is **streaming-first**, then REST, then TA/Chartist, and to wire all current users of `underlying_price_hint` through that helper.

End state: when running `trade-ideas` in `STRATDECK_DATA_MODE=live`, the underlying price used in trade ideas is:

1. DXLink mid (via `TastyProvider.get_quote`) when available and fresh.
2. REST `/market-data` mid/mark (via `TastyProvider.get_quote`) when streaming is unavailable.
3. TA / Chartist hint as a final fallback.

---

## CONTEXT

Repo root: `~/Projects/stratdeck-copilot`

Environment:

- Python 3.11 venv (but code must remain 3.9-compatible).
- CLI: `python -m stratdeck.cli ...`
- Tests: `python -m pytest`

Existing relevant pieces:

- `stratdeck/agents/trade_planner.py`
  - Builds trade ideas and currently derives `underlying_price_hint` via a mix of:
    - Chartist/TA (from `stratdeck/tools/ta.py`).
    - Optional live Tasty quote calls (REST).
    - Some basic SPX/XSP mapping and caching.
  - This logic is a bit scattered and still assumes REST as “live”.

- `stratdeck/data/tasty_provider.py`
  - `TastyProvider` implements `get_quote(symbol: str) -> Dict[str, Any]` with **streaming-first** semantics in live mode:
    - Tries DXLink snapshot (`_quote_from_snapshot`).
    - Falls back to REST `/market-data/...`.
    - Returns a dict with keys like `symbol`, `bid`, `ask`, `last`, `mark`, `mid`, and possibly `source="dxlink"`.

- `stratdeck/data/live_quotes.py`
  - `LiveMarketDataService` wraps DXLink streaming and maintains an in-process snapshot cache.
  - Tied into `TastyProvider` via the `live_quotes` parameter.

- Tests:
  - `tests/test_trade_planner_underlying_price.py`
  - `tests/test_underlying_price_hint.py`
  - `tests/test_tasty_provider_live_quotes.py`

You must read and respect `AGENTS.md` (logging, style, error handling, tests).

---

## GOAL

**Centralise and clarify underlying price selection.**

Create a single, well-named helper function that encapsulates the logic for choosing `underlying_price_hint` with this precedence:

1. **Live quote via provider** (DXLink/REST), if a provider is available and returns a usable quote.
2. **TA / Chartist hint** based on the data symbol / chart symbol.
3. As a last resort, a safe, clearly logged fallback (e.g. TA-only value or 0.0 with a warning).

Then:

- Update `trade_planner` (and any other users) to call this helper instead of inlining mixed logic.
- Update / extend tests so the behaviour is explicit and verified.

---

## SCOPE

You **should** touch:

- `stratdeck/agents/trade_planner.py`
- `stratdeck/data/tasty_provider.py` (only if needed for a small helper reuse; do **not** change its public `get_quote` signature or streaming semantics)
- `tests/test_trade_planner_underlying_price.py`
- `tests/test_underlying_price_hint.py`
- Optionally add a tiny helper test to `tests/test_tasty_provider_live_quotes.py` if helpful.

You **must not**:

- Change CLI signatures (`stratdeck.cli` commands).
- Change `TastyProvider.get_quote` external contract (input/output shape).
- Break existing DXLink / live quote tests.
- Change strategy filters, POP, IVR, or chain-pricing logic in this task.

---

## NON-GOALS

- No orchestrator work.
- No new rate-limit / 429 cooldown logic (that’s a separate task).
- No new strategies or filters.
- No changes to how DXLink is initialised or shutdown.

This task is narrowly about **how we pick the underlying price for trade ideas**, using the existing live data plumbing.

---

## REQUIREMENTS

### 1. Introduce a dedicated underlying price helper

Create a single helper function in `stratdeck/agents/trade_planner.py` (or a very small adjacent module imported by it) with a clear, explicit signature. For example:

```python
from typing import Optional
from stratdeck.data.provider import IDataProvider
from stratdeck.tools.ta import ChartistEngine  # or whatever the actual type is

def resolve_underlying_price_hint(
    symbol: str,
    data_symbol: str,
    provider: Optional[IDataProvider],
    chartist: Optional["ChartistEngine"],
) -> float:
    """
    Resolve the underlying price hint for a trade idea, using this precedence:

      1. Live quote via provider.get_quote(symbol) when available.
      2. TA/Chartist hint on data_symbol (or symbol) when available.
      3. Safe fallback with logging if both are unavailable.

    Returns a float price value.
    """
    ...
```

Notes:

- `symbol` is the trade symbol (e.g. `SPX`, `XSP`).
- `data_symbol` is the chart/TA symbol (e.g. `^GSPC` for SPX/XSP index strategies).
- `provider` is the currently active `IDataProvider` (often a `TastyProvider`).
- `chartist` is whatever TA engine is being used by `trade_planner` today (don’t invent a new abstraction; reuse what exists).

Implementation specifics:

- **Live quote path**:
  - If `provider` is not `None`, call `provider.get_quote(symbol)` once.
  - From the returned dict, derive a price:

    ```python
    live_mid = quote.get("mid")
    live_mark = quote.get("mark")
    live_last = quote.get("last")

    live_price = first_non_none(live_mid, live_mark, live_last)
    ```

  - If `live_price` is not `None`, use this as the `underlying_price_hint`.
  - Do not attempt to re-implement streaming vs REST; `TastyProvider.get_quote` already handles that.

- **TA / Chartist path**:
  - If `chartist` is not `None`, use the existing API that `trade_planner` uses today to fetch a TA-based price hint for `data_symbol` (or `symbol` where appropriate).
  - This should be the same TA value you would have used previously; just pulled into this helper.

- **Fallback and logging**:
  - If both `live_price` and TA price are unavailable:
    - Log a warning with enough context: symbol, data_symbol, provider present or not.
    - Return a safe default (0.0 or some other minimal fallback consistent with existing behaviour).
  - Respect logging conventions in `AGENTS.md`.

- Make the precedence explicit in the code and docstring.

### 2. Wire trade planner through the helper

- Find all places in `stratdeck/agents/trade_planner.py` where `underlying_price_hint` is currently computed or manipulated.
- Replace ad-hoc logic (TA-only, direct REST calls, etc.) with a call to `resolve_underlying_price_hint(...)`.
- Ensure this helper is used for both SPX and XSP (and any other index strategies) using the correct `symbol` / `data_symbol` pairing.
- Ensure that the existing public surface (what `trade-ideas` emits) remains the same shape:
  - Trade idea dict still contains `underlying_price_hint: float`.

### 3. Keep provider responsibilities clean

- Do **not** move TA/Chartist logic into `TastyProvider`.
- Do **not** change `TastyProvider.get_quote` signature or how it uses DXLink vs REST.
- If you find any duplicated “pick mid/mark/last” logic in `trade_planner`, replace it with a small internal helper function or reuse what exists; avoid copy-pasting.

### 4. Tests

Update and/or add tests to cover the new helper and behaviour:

- In `tests/test_trade_planner_underlying_price.py` and/or `tests/test_underlying_price_hint.py`:

  1. **Live price available, TA also available**  
     - Use a fake provider whose `get_quote` returns a dict with `mid` set to e.g. 123.45, and a fake TA engine that returns a different value (e.g. 120.0).
     - Assert that `resolve_underlying_price_hint(...)` returns **the live value** (123.45).

  2. **No provider or provider fails → fall back to TA**  
     - Fake provider raises or returns `None` / empty dict, but TA returns 120.0.
     - Assert that the helper returns 120.0.

  3. **No live price and no TA**  
     - Provider returns a dict with no usable price fields, and TA returns `None` or is absent.
     - Assert that the helper:
       - Returns a fallback value consistent with your implementation (e.g. 0.0).
       - Logs a warning (you can assert via `caplog` if already used in the repo, or at least keep the code structured for easy logging).

  4. **Quote dict variations**  
     - Cases where `mid` is `None` but `mark` is set.
     - Cases where only `last` is set.
     - Ensure the helper picks the first non-None value in the intended order.

- Keep tests **network-free**:
  - Do not call real Tastytrade APIs.
  - Use fake/mocked providers and TA engines.

- All existing tests in the repo must remain green after your changes:

  ```bash
  python -m pytest
  ```

  If expectations of existing tests need updating to match the new, clearer semantics, do so carefully and explain in comments if needed.

---

## WORKFLOW

1. **Read `AGENTS.md`** to align on style, logging, and testing expectations.

2. **Inspect current underlying price logic:**
   - `stratdeck/agents/trade_planner.py`
     - Find all code relating to `underlying_price_hint`, TA price hints, and any direct `get_quote` usage.
   - `stratdeck/tools/ta.py`
     - Identify the API used for TA price hints.
   - `stratdeck/data/tasty_provider.py`
     - Confirm the shape of `get_quote` return values and any existing “mid/mark/last” helper logic.
   - Tests:
     - `tests/test_trade_planner_underlying_price.py`
     - `tests/test_underlying_price_hint.py`
     - `tests/test_tasty_provider_live_quotes.py` for reference of quote dict structure.

3. **Implement `resolve_underlying_price_hint(...)`:**
   - Add the helper as described in `trade_planner.py` (or a small adjacent module imported by it).
   - Implement the precedence logic (live → TA → fallback).
   - Add docstring and type hints.

4. **Refactor `trade_planner` to use the helper:**
   - Replace inline logic with calls to `resolve_underlying_price_hint(...)`.
   - Ensure all code paths that produce trade ideas set `underlying_price_hint` via this helper.

5. **Update / extend tests:**
   - Adjust existing tests to call the new helper (or exercise it indirectly via the planner).
   - Add the scenarios listed in the Requirements section.
   - Keep tests isolated from real network I/O.

6. **Run tests:**

   ```bash
   python -m pytest
   ```

7. **Show diff:**
   - Summarise what you changed.
   - Print the `git diff` for the modified files.

---

## ACCEPTANCE CRITERIA

- There is a single, clearly named helper responsible for resolving `underlying_price_hint` with the precedence:

  1. Live quote via `provider.get_quote(symbol)` (DXLink/REST under the hood).
  2. TA/Chartist hint.
  3. Logged fallback.

- `trade_planner` (and any other relevant callers) use this helper instead of ad-hoc logic.

- When running:

  ```bash
  export STRATDECK_DATA_MODE=live
  python -m stratdeck.cli trade-ideas \
    --strategy short_put_spread_index_45d \
    --universe index_core \
    --json-output
  ```

  the generated trade ideas still contain an `underlying_price_hint` field, and its value is derived through the new helper (i.e. live quotes when available, otherwise TA).

- All tests pass:

  ```bash
  python -m pytest
  ```

- No regressions in:
  - DXLink streaming behaviour.
  - `TastyProvider.get_quote` interface.
  - Existing trade idea structure.
