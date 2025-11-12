# StratDeck CLI (v0.3 – Live Scout)

StratDeck is now a pure Python CLI that can scan candidates, run compliance, log paper fills, and summarize your journal. Mock mode remains the default; flip `STRATDECK_DATA_MODE=live` to pull tastytrade chains, quotes, balances, and positions.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .  # or pip install -r requirements.txt if you prefer

# copy env template and edit credentials / mode
cp .env.example .env
# set STRATDECK_DATA_MODE=mock to stay fully local

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

`enter --confirm` prints both the broker ticket id (from the simulated fill) and the ledger position id recorded in `positions.csv`. Use that ID with the `close` command.

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
