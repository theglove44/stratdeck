# AGENTS.md – StratDeck Copilot

You are an AI coding agent working on **StratDeck Copilot**, an options-trading research and execution tool.
Your job is to improve the codebase **safely**, keep tests green, and respect the trading constraints below.

---

## 1. Project overview

- **Language:** Python 3.9
- **Entry point:** `python -m stratdeck.cli ...`
- **Domain:** Options trading (Tastytrade-style mechanics, defined-risk spreads, iron condors, etc.)
- **Goal:** Multi-agent trading system (Scout / Trader / Risk / Compliance / Journal / Chartist / TradePlanner) that:
  - Scans candidates, enriches them with technical analysis, and converts them into structured trade ideas.
  - Prices chains and computes metrics (POP, credit/width, greeks).
  - Simulates or previews orders (paper only by default; live preview/place remains optional/stubbed).
  - Tracks positions, P&L, and journal entries in CSV-ledgers.

This is a **trading system**, not a generic web app. Any work you do must preserve risk controls and never place real trades without explicit instructions.

---

## 2. Setup & environment

### 2.1 Install / update dependencies

```bash
python -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt
```

If a `pyproject.toml` or `requirements-dev.txt` exists, prefer those for dev dependencies.

### 2.2 Environment assumptions

When running commands, assume:

- Working directory: repo root (contains `stratdeck/`).
- Safe environment variables:
  - `STRATDECK_DATA_MODE`:
    - `mock` = use cached/simulated data (preferred for tests).
    - `live` = use real market data (only if explicitly asked).
  - `STRATDECK_DEBUG_STRATEGY_FILTERS`:
    - `1` enables verbose logging for strategy filters.
- `.env` is auto-loaded by `stratdeck/__init__.py`; copy `.env.example` and set:
  - `TASTY_USER`/`TASTY_PASS` (or `TT_USERNAME`/`TT_PASSWORD`) for live mode.
  - `TASTY_ACCOUNT_ID` optionally (defaults resolved automatically).

If you need to set env vars in examples, **default to mock/paper mode**:

```bash
export STRATDECK_DATA_MODE=mock
```

Never read or touch any real API keys or secrets unless the user explicitly asks you to and explains the risk trade-offs.

---

## 3. Commands you should run

### 3.1 Health check

Use this as the first sanity check before or after bigger changes:

```bash
python -m stratdeck.cli doctor
```

If `doctor` fails, fix the underlying problem instead of patching around it.

### 3.2 Tests

Preferred test commands (in order):

```bash
# full test suite
python -m pytest

# or, if a tests/ package is present:
pytest tests

# for focused work, you may run single modules:
pytest tests/test_strategies.py
pytest tests/test_tools_chains.py
```

Your default behavior:

- After editing code, **run pytest**.
- If tests fail, inspect the failures, fix the root cause, and rerun until green.

### 3.3 CLI sanity checks

These are representative CLI entrypoints you can use to validate behavior:

```bash
# scan and rank candidates (mock by default; live chains/quotes when enabled)
python -m stratdeck.cli scan --top 5

# compliance + paper fill (append --live-order only if explicitly asked)
python -m stratdeck.cli enter --pick 1 --qty 1 --confirm

# TA + trade-idea pipeline
python -m stratdeck.cli chartist -s SPX -s XSP --json-output
python -m stratdeck.cli scan-ta --json-output
python -m stratdeck.cli trade-ideas --json-output ./ideas.json

# journal / ledger helpers
python -m stratdeck.cli positions
python -m stratdeck.cli close --position-id 1 --exit-credit 0.5
python -m stratdeck.cli report --daily

# check the trading pipeline is wired correctly (paper / mock only)
python -m stratdeck.cli doctor
```

Do **not** invent new CLI flags unless you’ve checked `stratdeck/cli.py` and ensured they’re consistent.

---

## 4. Project structure & responsibilities

High-level layout (from the perspective of an AI agent):

- `stratdeck/cli.py`  
  Click-based CLI entrypoint. Treat existing commands and options as a **public API**.  
  - You may add new subcommands, but avoid breaking existing ones without a clear migration.

- `stratdeck/agents/`  
  Scout, Trader, Risk, Compliance, Journal, TradePlanner, and related agent orchestration.

- `stratdeck/strategy_engine.py`  
  - Builds strategy assignments and symbol tasks.
  - Orchestrates universe/strategy combinations.

- `stratdeck/strategies.py`  
  - Pydantic models defining:
    - Strategy templates (e.g. `short_put_spread_index_45d`).
    - DTE rules, width rules, filters (IVR, price, liquidity).
  - Any change here affects how trade ideas are generated.

- `stratdeck/tools/`
  - `chains.py` – option chain retrieval (mock vs live).
  - `pricing.py` & `chain_pricing_adapter.py` – pricing, POP, credit per width, greeks.
  - `orders.py` – order preview / (paper) placement.
  - `positions.py` – position store / CSV or DB logging.
  - `reports.py` – summaries / P&L.
  - `scan_cache.py` – caching scans / trade ideas.
  - `ta.py`, `chartist.py`, `vol.py`, `greeks.py` – technical analysis & volatility tools powering ChartistAgent and TA summaries.

- `stratdeck/conf/`  
  Prompt templates (e.g., `prompts/chartist_system.md`, `chartist_report.md`) and configuration.

- `stratdeck/config/` & `conf/`  
  YAML / config files for:
  - Universes (e.g. `index_core`).
  - Strategies & defaults.
  - Risk limits.

- `stratdeck/data/`  
  Mock/ledger CSVs (`positions.csv`, `journal.csv`) used for paper mode and reporting.

- `tests/`  
  Python unit / integration tests.  
  **Always update or add tests** when you change behavior.

---

## 5. Coding conventions

- **Style:** Follow the existing code style in this repo.
  - Prefer clear, explicit code over clever one-liners.
  - Type hints are encouraged where they’re already used.
- **Pydantic v2:** When modifying models:
  - Maintain existing field names and types when possible.
  - Keep model validation strict for external inputs (API, CLI args, config).
- **Errors & logging:**
  - Use Python exceptions for real error conditions.
  - Prefer structured, concise log messages over noisy prints.
  - For CLI commands, surface user-facing errors via clean messages and non-zero exit codes.

If you’re unsure between two stylistic options, copy the dominant pattern in the file you’re editing.

---

## 6. House rules for AI agents

These rules matter more than code style. Follow them strictly.

### 6.1 Safety & trading constraints

- **Default to non-destructive modes:**
  - Use `STRATDECK_DATA_MODE=mock` unless explicitly told otherwise.
  - If a trading mode exists (e.g. `STRATDECK_TRADING_MODE`), assume/force `paper` in examples.

- **Never:**
  - Add or change code to place **live trades** without explicit instructions.
  - Hard-code real account IDs, API keys, or secrets.
  - Remove existing safety checks around position sizing, delta exposure, or buying-power usage.
  - Default to `--live-order` or otherwise bypass paper-ledger flow without consent.

- **OK to do:**
  - Improve the **paper trading** pipeline.
  - Enhance logging for risk/compliance.
  - Add metrics / reports around P&L, win rate, drawdowns, etc.

### 6.2 Git & change scope

- Work as if you’re on a feature branch (e.g. `codex/autofix-YYYYMMDD`).
- Prefer **small, focused changes**:
  - Fix a failing test and its root cause.
  - Refactor a single module to reduce duplication.
  - Add a new strategy template with corresponding tests.
- When you finish a task, summarise:
  - What changed.
  - Why.
  - Any impacts on public CLI, configs, or strategies.

### 6.3 Strategy & risk logic

When editing strategy- or risk-related code:

- Preserve the **intent** of existing strategies:
  - Index strategies (e.g. SPX/XSP 45DTE spreads) should remain defined-risk.
  - Don’t silently widen spreads or increase leverage.
- Any change to:
  - Spread width rules,
  - IVR filters,
  - DTE targeting,
  must be documented in comments and ideally covered by tests.

---

## 7. Tasks you are allowed to perform

Examples of tasks that are **in scope** for you:

- **Test maintenance**
  - Run `pytest` and fix failing tests.
  - Add tests for untested modules before refactoring them.

- **Refactors / quality**
  - Deduplicate repeated pricing / IVR / greeks logic.
  - Improve function & variable naming for clarity.
  - Split overly long functions into smaller pieces.

- **Strategy engine improvements**
  - Add new strategy templates using the existing patterns.
  - Improve filtering (e.g. min IVR) while keeping behavior test-covered.
  - Add provenance / metadata to trade ideas (where they came from, which strategy, config, filters used).

- **Tooling & DX**
  - Add helper scripts (e.g. `scripts/dev/run_doctor_and_tests.sh`).
  - Improve error messages when APIs / data sources fail.

If you’re unsure whether a task is safe, prefer **adding tests and documentation** over changing core behavior.

---

## 8. Tasks out of scope (without explicit instruction)

Do **not** do these things unless the user explicitly asks and defines the constraints:

- Wire StratDeck to place **real-money orders**.
- Change the core CLI interface in a way that would break existing usage.
- Introduce new external dependencies that require system-level packages without explaining why.
- Perform destructive file operations outside this repo (e.g. editing dotfiles, system configs).

---

## 9. How to think about this project

When in doubt, optimise for:

1. **Correctness** – tests passing, clear invariants.
2. **Safety** – no unexpected live trades; paper/mocked by default.
3. **Clarity** – code others can read and extend.
4. **Extensibility** – new strategies and agents should drop in with minimal friction.

Your job is to be a careful, helpful teammate – not a cowboy refactor bot.
