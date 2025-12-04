# StratDeck Feature Spec – Vetting View for Trade Ideas (No-Assumptions Version)

**Feature name:** Vetting View for Trade Ideas  
**Target branch:** `feature/ideas-vet-view`  
**Repo root (as per user):** `~/Projects/stratdeck-copilot`  
**CLI entrypoint (as per user):** `python -m stratdeck.cli`  
**Python:** 3.9+  
**Tests:** `pytest -q`

This spec is intentionally written to **avoid assumptions about internal layout, naming, or structure** beyond what the user has explicitly stated.  
Every time you need to integrate with existing code, you MUST:

- Discover the relevant files/classes/functions by searching the repo.
- Adapt to the actual names and locations you find.
- Avoid duplicating or shadowing existing concepts (e.g. if a `vetting` module already exists, extend it instead of creating a new, conflicting one).

---

## 1. Discovery First – No-Assumptions Ground Rules

Before creating or modifying anything, perform a discovery pass to understand the real structure.

### 1.1. Confirm basic project shape

From the repo root (`~/Projects/stratdeck-copilot`):

1. List top-level contents:

   ```bash
   ls
   ```

   Confirm there is a `stratdeck` package directory and a `tests` directory (or similar). DO NOT assume their exact names; use `ls` to see what’s there and note the actual layout.

2. Confirm the CLI entrypoint works and see its help:

   ```bash
   python -m stratdeck.cli --help
   ```

   From this:

   - Identify what CLI framework is used (Typer, Click, argparse, etc.).
   - Confirm there is a `trade-ideas` style command and an `ideas-vet` or similarly named command (the user said there is an existing `ideas_vet` implementation or stub).

   **Rule:** Reuse the existing CLI style (decorators, grouping, option conventions) exactly as seen in the current `cli` implementation.

### 1.2. Locate the existing `ideas_vet` command

Use ripgrep or similar:

```bash
rg "ideas_vet" -n
rg "ideas-vet" -n
rg "ideas vet" -n
rg "vet" stratdeck -n
```

You are trying to find:

- The CLI command name for vetting ideas.
- Any stub / existing implementation, most likely in `stratdeck/cli.py` or a nearby module.

**Rules:**

- If there is already a function or command named `ideas_vet` (or similar), **extend and refactor that** rather than creating a new command with a different name.
- If there is no such command, you may add a new `ideas-vet` command to the CLI, but its name and style must be consistent with how other commands (e.g. `trade-ideas`) are defined.

### 1.3. Locate the `TradeIdea` model

We know there is a `TradeIdea` Pydantic model (per user), but we do NOT assume its location.

Use:

```bash
rg "class TradeIdea" -n
rg "TradeIdea" stratdeck -n
```

Once found:

- Note the module path, e.g. `stratdeck/domain/trade_ideas.py` or similar.
- Inspect the class:

  - Confirm fields such as: `symbol`, `strategy_id` (or other strategy identifier), `strategy_type`, `direction`, `dte`, `spread_width`, `ivr`, `pop`, `credit_per_width`, `trend_regime`, `vol_regime`, `short_legs`, `long_legs`, and leg-level `delta` and `dte`.
  - If the naming differs (e.g. `strategy_name` instead of `strategy_id`, `trendRegime` instead of `trend_regime`, etc.), all later code must adapt to those **actual** names.

**Rule:** All integrations MUST reference the real attribute names from this class; do not introduce parallel structures that duplicate the same semantics.

### 1.4. Locate human rules / strategy configuration

The user stated:

- A `strategies.yaml` file encodes human rules.
- A “human_rules filter module” enforces them.

Steps:

1. Locate the YAML:

   ```bash
   find . -iname "strategies.yaml"
   ```

   Note its actual path (for example, `stratdeck/config/strategies.yaml` or similar). DO NOT assume where it lives.

2. Locate the module that enforces human rules:

   ```bash
   rg "strategies.yaml" -n stratdeck
   rg "human_rules" -n stratdeck
   rg "apply_human_rules" -n stratdeck
   rg "IVR floor" -n stratdeck
   ```

   From these, identify:

   - The module(s) that parse `strategies.yaml`.
   - The function(s)/class(es) that enforce rules (IVR floors, DTE windows, POP floors, etc.) on `TradeIdea` data.

**Rule:** Any new “rules snapshot” or vetting adapter must build on top of this existing logic, not reimplement parsing or thresholds from scratch.

### 1.5. Locate existing tests for CLI and trade ideas

Use:

```bash
ls tests
rg "trade-ideas" tests -n
rg "TradeIdea" tests -n
rg "CLI" tests -n
```

Identify:

- How CLI testing is done (Typer’s `CliRunner`, Click’s `CliRunner`, straight subprocess, etc.).
- Any tests that already touch `trade-ideas` and/or the idea generation pipeline.

**Rule:** New tests must follow the existing structure and style so they integrate cleanly with the current suite.

---

## 2. Design: Core Vetting View (Conceptual)

This section defines **behaviour and responsibilities**; implementation details (names, paths) must be adapted to actual project structure discovered in §1.

### 2.1. Conceptual data model

We want a vetting layer that, given a `TradeIdea` and the relevant human-rule config for its strategy, produces:

- A **score** (e.g. 0–100).
- A **verdict**: ACCEPT / BORDERLINE / REJECT.
- A **rationale**: a short, human-readable one-liner.
- A **reasons** list: bullet-style explanations that explicitly reference the human rules.

Conceptually:

```python
class VetVerdict(str, Enum):
    ACCEPT = "ACCEPT"
    BORDERLINE = "BORDERLINE"
    REJECT = "REJECT"


class IdeaVetting(BaseModel):
    score: float
    verdict: VetVerdict
    rationale: str
    reasons: List[str]
```

> **Important:**  
> If an `IdeaVetting` or equivalent type already exists, extend/refine it rather than introducing a competing one.  
> Use `rg "IdeaVetting"` or similar to confirm.

### 2.2. Inputs for vetting

We need a clear adapter from:

- `TradeIdea` instance  
- + “rules snapshot” for that strategy

into a normalized “input bag” that the scoring logic can consume.

Conceptually:

```python
class VettingInputs(BaseModel):
    # From TradeIdea (exact names must match the actual model)
    symbol: str
    strategy_id: str  # or whatever field on TradeIdea uniquely identifies the strategy
    strategy_type: str
    direction: str

    dte: int
    spread_width: float
    short_delta: Optional[float]
    ivr: Optional[float]
    pop: Optional[float]
    credit_per_width: Optional[float]
    trend_regime: Optional[str]
    vol_regime: Optional[str]

    # From human rules / strategies.yaml (exact names derived from config)
    dte_target: Optional[int]
    dte_min: Optional[int]
    dte_max: Optional[int]

    expected_spread_width: Optional[float]

    target_short_delta: Optional[float]
    short_delta_min: Optional[float]
    short_delta_max: Optional[float]

    ivr_floor: Optional[float]
    pop_floor: Optional[float]
    credit_per_width_floor: Optional[float]

    allowed_trend_regimes: Optional[List[str]]
    allowed_vol_regimes: Optional[List[str]]
```

Implementation detail:

- The **actual field names** must match the real config structures.  
- If your strategies config encodes fields differently (e.g. `delta_band`, `spread_width_rule`), create a mapping layer that translates those into the above conceptual fields.

### 2.3. Rules snapshot from strategies.yaml

Design a “snapshot” model that represents the thresholds used by human rules, and a helper function to build it for a given strategy.

**You MUST:**

1. Inspect the existing code where strategies are parsed and human rules are applied to candidate ideas.
2. Identify the canonical representation of a *single strategy’s rules*.
3. If such a representation already exists, reuse or lightly wrap it.

If no suitable representation exists yet:

- Create a small Pydantic model in the **same module that currently handles human rules** (or a closely related module). For example:

  ```python
  class StrategyRuleSnapshot(BaseModel):
      strategy_key: str  # e.g. strategy_id or the config key
      dte_target: Optional[int]
      dte_min: Optional[int]
      dte_max: Optional[int]
      expected_spread_width: Optional[float]
      target_short_delta: Optional[float]
      short_delta_min: Optional[float]
      short_delta_max: Optional[float]
      ivr_floor: Optional[float]
      pop_floor: Optional[float]
      credit_per_width_floor: Optional[float]
      allowed_trend_regimes: Optional[List[str]] = None
      allowed_vol_regimes: Optional[List[str]] = None
  ```

Add a function (name and location must match existing style in that module) that:

- Accepts the strategy identifier used on `TradeIdea` (e.g. `strategy_id`, `strategy_key`, etc.).
- Looks up the corresponding config in `strategies.yaml`.
- Builds and returns a `StrategyRuleSnapshot`.

Example signature:

```python
def snapshot_for_strategy(strategy_key: str) -> StrategyRuleSnapshot:
    ...
```

**Rules:**

- Reuse the existing YAML loading and validation; do NOT re-open the YAML from scratch in multiple places.
- If there is already a helper that returns something like “strategy config”, consider adding a method or adapter to turn that into `StrategyRuleSnapshot`.

---

## 3. Implementation Tasks

### 3.1. Create/extend a vetting module

Locate any existing modules whose name suggests vetting, scoring, or evaluation:

```bash
find stratdeck -maxdepth 3 -type f | rg "vet" -
rg "score" stratdeck -n
```

- If you find a natural existing place to put this logic (e.g. `stratdeck/vetting.py`, `stratdeck/trade_ideas/vetting.py`, etc.), extend that module.
- If there is **no suitable existing module**, create a new one following the project’s naming conventions. For example, if other feature modules are at `stratdeck/<feature>.py`, you might add `stratdeck/vetting.py`. If features are organised into subpackages, follow that structure.

Within that module, implement:

1. `VetVerdict` enum.
2. `IdeaVetting` Pydantic model.
3. `VettingInputs` Pydantic model.
4. A function (names to match project style), conceptually:

   ```python
   def build_vetting_inputs(idea: TradeIdea, rules: StrategyRuleSnapshot) -> VettingInputs:
       """Adapt a TradeIdea and its associated strategy rules into a VettingInputs bag.
       Use *actual* attribute names from TradeIdea and StrategyRuleSnapshot."""
       ...
   ```

5. A pure scoring function, e.g.:

   ```python
   def vet_from_inputs(inputs: VettingInputs) -> IdeaVetting:
       """Pure, deterministic scoring + rationale builder.
       No file I/O, no network, no CLI."""
       ...
   ```

6. Convenience wrappers:

   ```python
   def vet_single_idea(idea: TradeIdea, rules: StrategyRuleSnapshot) -> IdeaVetting:
       ...
   ```

   ```python
   def vet_batch(
       ideas: Sequence[TradeIdea],
       rules_lookup: Callable[[str], StrategyRuleSnapshot],
   ) -> List[Tuple[TradeIdea, IdeaVetting]]:
       ...
   ```

**Scoring rules (behavioural, not hardcoded names):**

- **REJECT** if any hard human rule is violated:
  - DTE outside config window.
  - Spread width not matching expected width rule.
  - IVR below floor.
  - POP below floor.
  - Credit/width below floor.
  - Short delta outside allowed band.
  - Disallowed trend or vol regime (if configured).
- **BORDERLINE** if candidate passes but hugs one or more thresholds:
  - e.g. IVR within a small epsilon of floor, credit_per_width barely above floor, DTE at edge of allowed window.
- **ACCEPT** if candidate passes comfortably.

Score:

- Start from a baseline (e.g. 50).
- Add points for how comfortably above thresholds the metrics are.
- Clamp to [0, 100].
- Exact scoring weights can be simple but must be consistent and deterministic.

Rationale:

- Build a concise one-liner in the style:

  > "ADBE: passes 45DTE window, 5-wide, ~30Δ short put, IVR 0.45 > 0.30 floor, POP 0.62 > 0.55 floor, credit_per_width 0.28 > 0.25 floor, trend uptrend, vol normal – ACCEPT."

Reasons list:

- Bullet-style strings that explicitly reference rule thresholds and actual values.
- On REJECT, reasons MUST clearly indicate which rule(s) were violated, e.g.:

  - "IVR 0.24 is below floor 0.30 – violates human rule."
  - "DTE 37 is outside allowed window [40, 50]."

### 3.2. Wire vetting into the CLI `ideas-vet` command

1. Open the CLI module (found earlier via `python -m stratdeck.cli --help` and `rg`):

   - Typically `stratdeck/cli.py`, but use the actual file you discovered.

2. Identify:

   - How commands are declared (Typer, Click, etc.).
   - The existing definition for the ideas vetting command (if any).

3. Extend/implement the `ideas-vet` command so that it:

   - Accepts an optional path to an ideas JSON file (default: `.stratdeck/last_trade_ideas.json` in repo root, unless the existing code uses a different default – follow existing behaviour).
   - Accepts a `--json-output` (or equivalent) flag consistent with how `trade-ideas` implements JSON output.
   - Optionally accepts a `--sort-by` parameter with allowed values `"symbol"` and `"score"`.

4. Implementation steps inside the command:

   1. Resolve the ideas file path:

      - If an option is provided, use it.
      - Otherwise, use the same default path the existing `trade-ideas` command writes to (from user: `.stratdeck/last_trade_ideas.json`), **but confirm this by inspecting the existing code**.

   2. Load the file as JSON:

      ```python
      data = json.loads(path.read_text())
      ```

      Make no assumptions about JSON structure beyond what `trade-ideas` writes; instead, confirm by inspecting that command’s JSON mode.

   3. Rehydrate `TradeIdea` objects:

      - Use `TradeIdea`’s actual Pydantic API (`model_validate`, `parse_obj`, or constructor, depending on existing style).

   4. Prepare a `rules_lookup` function that:

      - Accepts the strategy identifier field from `TradeIdea` (e.g. `idea.strategy_id`, `idea.strategy_key`, etc. – confirm real name).
      - Calls the `snapshot_for_strategy` or equivalent helper you added in the human rules module.
      - Returns a `StrategyRuleSnapshot`.

   5. Call `vet_batch(ideas, rules_lookup)` from the vetting module.

   6. Sort results:

      - If sort by `"score"`, sort descending by `IdeaVetting.score`.
      - If sort by `"symbol"`, sort by `TradeIdea.symbol`.

   7. Output:

      - **JSON mode:**
        - For each `(idea, vetting)` pair, call the existing `TradeIdea` JSON serialization method used by `trade-ideas` (`model_dump(mode="json")` or equivalent).
        - Add a `"vetting"` key with `IdeaVetting` serialized in the same style.
        - Print the list via the CLI framework (e.g. `typer.echo(json.dumps(...))`).
      - **Human-readable mode:**
        - Print a header row with at least:
          - symbol
          - strategy identifier
          - direction
          - dte
          - spread width
          - short leg delta
          - ivr
          - pop
          - credit_per_width
          - trend_regime
          - vol_regime
          - verdict
        - For each idea, print:
          - A compact line with these metrics.
          - A second line (indented) containing `vetting.rationale`.
        - Use formatting conventions consistent with the existing CLI’s tabular output (spacing, color, etc.).

### 3.3. Behavioural examples (for manual testing)

Once implemented, from repo root:

#### 3.3.1. Mock mode end-to-end

```bash
export STRATDECK_DATA_MODE=mock

# Step 1: generate ideas (use whatever universe/strategy exists in your config)
python -m stratdeck.cli trade-ideas \
  --universe index_core \
  --strategy short_put_spread_index_45d \
  --json-output > .stratdeck/last_trade_ideas.json

# Step 2: run vetting in human-readable mode
python -m stratdeck.cli ideas-vet

# Step 3: run vetting with JSON output
python -m stratdeck.cli ideas-vet --json-output > /tmp/vetted_ideas.json
```

Check that:

- Human-read mode prints one row per idea, with verdict and rationale.
- JSON mode produces a list where each object has a `"vetting"` key with `score`, `verdict`, `rationale`, and `reasons`.

#### 3.3.2. Vet a specific ideas file

```bash
python -m stratdeck.cli ideas-vet \
  --ideas-path /tmp/ideas_equity_put_mock.json \
  --sort-by score \
  --json-output > /tmp/vetted_equity_put_ideas.json
```

---

## 4. Tests

All new tests must live alongside the existing test suite and follow its conventions (naming, fixtures, helper usage).  
Before adding tests, inspect current patterns:

```bash
ls tests
rg "trade-ideas" tests -n
rg "cli" tests -n
rg "pytest" tests -n
```

### 4.1. Core vetting tests

Create a new test module in `tests/` (or appropriate package) for the vetting core. Example name: `tests/test_vetting_core.py`, unless the project uses a different convention. Adapt the name to match existing files.

Tests to include:

1. **Strong candidate → ACCEPT**

   - Build a `VettingInputs` instance directly (do not depend on TradeIdea in this test).
   - Set metrics comfortably within rule windows:
     - DTE well inside [min, max].
     - Spread width matching expected.
     - short_delta within band.
     - IVR, POP, credit_per_width comfortably above floors.
   - Call `vet_from_inputs`.
   - Assert:
     - `verdict` is ACCEPT.
     - `score` is high relative to whatever baseline you implement (e.g. > 70).
     - `reasons` contain text describing the rule satisfaction for at least DTE and IVR.

2. **Rule violation → REJECT**

   - Clone the strong candidate, then set IVR below its floor (or DTE outside window).
   - Call `vet_from_inputs`.
   - Assert:
     - `verdict` is REJECT.
     - At least one `reason` clearly states that IVR (or DTE, whichever you used) violates the floor/window.

3. **Borderline case → BORDERLINE**

   - Candidate just above a floor, e.g. credit_per_width barely above its floor, or DTE at the exact edge of allowed window.
   - Call `vet_from_inputs`.
   - Assert:
     - `verdict` is BORDERLINE or `score` is noticeably lower than the strong candidate.
     - A `reason` string mentions that metric as borderline (explicitly use the word “borderline” or similar so tests can assert on it).

### 4.2. Integration tests (TradeIdea + rules)

Create a new test module, e.g. `tests/test_vetting_integration.py`, or integrate in an existing test module if your suite already has a “pipeline” or “trade ideas” integration test file.

Tests:

1. **TradeIdea + StrategyRuleSnapshot → IdeaVetting**

   - Use an existing fixture or factory that produces a realistic `TradeIdea` object for a known strategy, e.g. your 45DTE short put spread.
   - Use the new helper (`snapshot_for_strategy` or equivalent) to obtain the rules snapshot for that strategy.
   - Call `vet_single_idea(idea, rules)`.
   - Assert:
     - `verdict` is one of the valid enum values.
     - `rationale` is non-empty.
     - `reasons` list is non-empty.

### 4.3. CLI tests

Locate existing CLI tests (for `trade-ideas` or similar) and mirror their style. Example commands might use Typer’s `CliRunner`, Click’s `CliRunner`, or subprocess.

Create a new test file, e.g. `tests/test_cli_ideas_vet.py`.

#### 4.3.1. Human mode smoke test

- Arrange:

  - Generate a small ideas JSON file either by:
    - Calling `trade-ideas` in mock mode from the test (if that’s already done elsewhere in tests), or
    - Using an existing helper/fixture that writes a valid ideas file to a temporary path.

- Act:

  - Invoke CLI via existing pattern, e.g.:

    ```python
    result = runner.invoke(
        app,
        ["ideas-vet", "--ideas-path", str(ideas_path)],
    )
    ```

- Assert:

  - Exit code == 0.
  - Output contains column headers (e.g. “SYMBOL” and “Verdict” or whatever headers you actually used).
  - Output includes at least one line that looks like a vetted idea row plus rationale.

#### 4.3.2. JSON mode smoke test

- Arrange:

  - Same ideas file as above.

- Act:

  ```python
  result = runner.invoke(
      app,
      ["ideas-vet", "--ideas-path", str(ideas_path), "--json-output"],
  )
  ```

- Assert:

  - Exit code == 0.
  - `json.loads(result.stdout)` returns a list.
  - First element has a `"vetting"` key with nested keys: `"score"`, `"verdict"`, `"rationale"`, `"reasons"`.

---

## 5. Self-Check & Guardrails

Before opening a PR from `feature/ideas-vet-view` into `main`:

1. **No-assumptions verification**

   - Confirm that all imports refer to real, existing modules and classes.
   - Confirm that you did not introduce any unused or duplicate model types for concepts that already exist.
   - Confirm that naming of attributes in vetting logic matches the actual `TradeIdea` and config structures (no hardcoded guesses).

2. **Tests**

   - Run `pytest -q` from repo root and ensure all tests (existing + new) pass.
   - If any tests fail due to presumptions about layout or naming, fix the integration rather than changing tests to mask the issue.

3. **CLI manual tests**

   - Run the example commands from §3.3 in **mock** mode and verify:
     - Output is readable and clearly explains why each idea passes/fails.
     - JSON mode structure is correct and self-describing.

4. **Code quality**

   - Follow existing style: type hints, Pydantic version usage, logging style, and error handling.
   - Keep vetting logic pure and side-effect free; file I/O should remain in the CLI / orchestration layer.

Once all checks pass, raise the PR with a description summarising:

- Where the vetting logic lives.
- How it discovers thresholds from human rules.
- Example vetting output for one or two symbols.
