# StratDeck CLI (v0.3 – Live Scout)

StratDeck is now a pure Python CLI that can scan candidates, run compliance, log paper fills, and summarize your journal. Mock mode remains the default; flip `STRATDECK_DATA_MODE=live` to pull tastytrade chains, quotes, balances, and positions.

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .  # or pip install -r requirements.txt if you prefer

# copy env template and edit credentials / mode
cp .env.example .env
# set STRATDECK_DATA_MODE=mock to stay fully local

# From this point onward we assume `python` points to your Python 3 interpreter.
# If your shell lacks a `python` binary, keep using `python3` (e.g. `python3 -m stratdeck.cli scan --top 5`).
python -m stratdeck.cli scan --top 5
```

### Environment

`.env` is auto-loaded by `stratdeck/__init__.py`. Relevant keys:

| Key | Description |
| --- | --- |
| `STRATDECK_DATA_MODE` | `mock` (default) or `live` |
| `TASTY_USER` / `TASTY_PASS` | tastytrade username/password (live mode) |
| `TT_USERNAME` / `TT_PASSWORD` | optional aliases |
| `TASTY_ACCOUNT_ID` | optional; autodetected when omitted |

Mock mode uses the offline heuristics + CSV ledgers in `stratdeck/data/`. Live mode logs into tastytrade (session token stored in-memory only) and fetches nested chains, option quotes (with greeks), balances, and positions.

## CLI Cheat Sheet

| Command | Purpose |
| --- | --- |
| `python -m stratdeck.cli scan --top N` | Rank candidates (now backed by live chains when enabled) |
| `python -m stratdeck.cli enter --pick i --qty q [--confirm] [--live-order]` | Run compliance on the `i`‑th scan result; `--confirm` logs a paper fill, `--live-order` calls tastytrade preview/place (still optional) |
| `python -m stratdeck.cli positions` | List paper ledger entries from `data/positions.csv` |
| `python -m stratdeck.cli close --position-id ID --exit-credit X` | Close a paper trade, compute realized P/L, journal it |
| `python -m stratdeck.cli report --daily` | Summarize opens/closes, win rate, realized P/L, and live balances |
| `python -m stratdeck.cli doctor` | Run diagnostics (env, provider reachability, config files) |
| `python -m stratdeck.cli chartist -s SYMBOL [--json-output]` | Run ChartistAgent technical analysis for one or more symbols and emit either a fallback summary or JSON `TA_RESULT` maps |
| `python -m stratdeck.cli scan-ta [--json-output]` | Run Scout → Chartist to score candidates, print TA-enriched data, or dump the JSON for automation |
| `python -m stratdeck.cli trade-ideas [--json-output]` | Scout → Chartist → TradePlanner pipeline that outputs structured trade ideas with legs, rationale, and vol/trend context |

`enter --confirm` prints both the broker ticket id (from the simulated fill) and the ledger position id recorded in `positions.csv`. Use that ID with the `close` command.

## Chartist TA & trade idea pipeline

`ChartistAgent` now sits alongside the existing Scout/Trader stack. It wraps `ChartistEngine` (`stratdeck/tools/ta.py`), a deterministic technical-analysis core that classifies trend/volatility/regime, spots structure and simple patterns, computes TA scores, and returns a `TAResult` rich with `trend_regime`, `vol_regime`, `momentum`, `structure`, `scores`, and `options_guidance`. ChartistAgent can run with an optional LLM client and uses the prompts in `stratdeck/conf/prompts/chartist_system.md` and `chartist_report.md` to drive human summaries; if no LLM is provided it falls back to the built-in plain-text summary you see on the `chartist` command.

`ChartistEngine` is data-agnostic: it consumes `data_client` objects when supplied, defaults to synthetic mock candles (`STRATDECK_DATA_MODE=mock`), and, when running in live mode, will try to pull OHLCV from `yfinance` (install `yfinance` if you want live-chart candles without wiring a custom client). Missing data or yfinance failures gracefully revert to synthetic candles with a warning so the CLI remains runnable.

The new CLI helpers bridge Scout → Chartist → TradePlanner:

- `python -m stratdeck.cli chartist -s SPX -s XSP` runs ChartistAgent for each symbol, optionally accepts `--strategy-hint`, `--timeframe`, `--lookback-bars`, and emits either the fallback summary or the raw `TA_RESULT` JSON when `--json-output` is set.
- `python -m stratdeck.cli scan-ta` runs ScoutAgent and feeds the candidate list through ChartistAgent to produce TA-enriched rows (`ta_score`, `ta_directional_bias`, `ta_vol_bias`, support/resistance, scoring metadata). By default it prints a simple table; add `--json-output` to capture the entire enriched payload for other tooling.
- `python -m stratdeck.cli trade-ideas` pipes Scout → Chartist → `TradePlanner` (`stratdeck/agents/trade_planner.py`). TradePlanner converts the TA context into `TradeIdea` structs with legs, rationale, notes, and underlying hints. The command prints human-readable trade ideas but you can rerun it with `--json-output` (optionally appending a path to persist the JSON, e.g. `python -m stratdeck.cli trade-ideas --json-output ./ideas.json`) to consume the structured output elsewhere (e.g., TraderAgent, journaling scripts, or a RiskAgent).

The Chartist prompts in `stratdeck/conf/prompts/chartist_system.md` and `chartist_report.md` are the place to tweak how an LLM interprets the deterministic TA data, while the `TAResult`/`TradeIdea` JSONs remain machine-friendly for automation.

## Live Mode Notes

- Credentials are posted to `https://api.tastyworks.com/sessions`; the bearer token lives only in memory. Nothing is written to disk except the existing paper-ledger CSVs.
- Chains come from `/option-chains/<symbol>/nested` and option quotes from `/market-data/by-type?equity-option=…`. We currently limit each scan to 75 strikes near the underlying.
- The scan output now includes the actual short/long strikes, live mid credit, delta, and tastytrade IVR. Compliance checks reuse those strikes when you call `enter`.
- Order preview/place endpoints are still stubbed; `--confirm` remains a paper-only simulation.
- macOS + Python 3.9 ships with LibreSSL, so urllib3 emits a warning (“supports OpenSSL 1.1.1+”). It’s harmless and will disappear once the project moves to a newer Python build.

## Paper Ledger + Reporting

- `stratdeck/data/journal.csv` logs OPEN/CLOSE events with JSON metrics (credit, qty, preview stats, realized P/L).
- `stratdeck/data/positions.csv` tracks OPEN/CLOSED status, exit credit, and P/L per position ID.
- `report --daily` (or `--days N`) aggregates those files and augments the summary with live balances when available.

## Diagnostics

`python -m stratdeck.cli doctor` now checks:
- Required folders/files in `stratdeck/`
- YAML configs load without errors
- When `STRATDECK_DATA_MODE=live`: env vars are present, tastytrade login succeeds, default account resolves, and a short chain request returns data

If any step fails you’ll see a bullet under “Doctor found issues”. Keep mock mode around for travel days or when the tastytrade API is unavailable.
