# StratDeck – Vetting Verdict Refinement (Delta Spec)

**Context:**  
The `feature/ideas-vet-view` branch already implements a vetting engine that:

- Computes a `score` (0–100).
- Assigns a `verdict` ("ACCEPT" | "BORDERLINE" | "REJECT").
- Produces a `rationale` and `reasons` list.
- Surfaces metrics like DTE, spread width, short delta, IVR, POP, credit-per-width, and regime context.

Example current behaviour for a strong GOOGL idea:

- `score`: 88.0  
- `verdict`: `"BORDERLINE"`  
- Metrics:  
  - DTE 45 within [40, 50]  
  - width 5.0 matches expected 5.0  
  - short delta 0.35 within [0.25, 0.35]  
  - IVR 0.27 >= floor 0.25  
  - POP 0.66 >= floor 0.60  
  - credit/width 0.30 >= floor 0.25  
- Reasons include:
  - "One or more metrics are borderline to configured floors/bands."
  - "trend_regime missing while allowlist configured"
  - "vol_regime missing while allowlist configured"

This is too conservative: a high-scoring, rule-compliant idea is being labelled BORDERLINE primarily because regime data is missing.

---

## Goal

Refine the **verdict classification logic** so that:

1. **Hard rule violations** still yield `REJECT`.
2. **Genuinely marginal candidates** near thresholds yield `BORDERLINE`.
3. **Strong candidates like the GOOGL example above** become `ACCEPT`, with regime issues treated as informational rather than automatically downgrading the verdict.

The numeric `score` computation can stay as-is; we mainly want to adjust how `verdict` is derived from:

- Hard failures
- Borderline flags
- Regime missing flags
- Score

---

## 1. Discovery – Find the Current Verdict Logic

From repo root (`~/Projects/stratdeck-copilot`):

1. Identify the vetting module used by `ideas-vet`:

   ```bash
   rg "trend_regime missing while allowlist configured" -n
   rg "vol_regime missing while allowlist configured" -n
   rg "One or more metrics are borderline to configured floors/bands." -n
   ```

   This should lead you to the core vetting/scoring function (for example something like `vet_from_inputs`, but use the **actual name and module** you find).

2. Inspect that function and note:

   - How it tracks:
     - Hard violations (below floor / outside band).
     - Borderline metrics (close to thresholds).
     - Regime-related issues.
   - How it finally chooses `verdict` based on these signals and/or the score.

---

## 2. Behavioural Changes – Classification Policy

Implement the following **behavioural policy** in the vetting core. Keep function names, signatures, and module locations as they currently exist; only change internal logic and tests.

### 2.1. Define categories of issues

Inside the vetting function, conceptually track:

- `hard_violations`: list of strings describing rule violations, e.g.:
  - IVR below floor
  - POP below floor
  - credit/width below floor
  - DTE outside [min, max]
  - short delta outside [min, max]
  - spread width not matching expected rule
- `borderline_flags`: list of strings describing metrics that are just above thresholds, e.g.:
  - IVR just above floor
  - POP just above floor
  - cpw just above floor
  - DTE at the edge of the window
- `regime_flags`: list of strings for regime context issues, e.g.:
  - trend_regime missing while allowlist configured
  - vol_regime missing while allowlist configured
  - (or equivalent phrasing used in current code)

You do NOT need to rename these variables exactly; just track them explicitly in whatever structure you are already using.

### 2.2. Verdict decision rules

After computing `score` and filling the three lists above, choose `verdict` using the following precedence:

1. **If `hard_violations` is non-empty → `REJECT`**

   - `verdict = "REJECT"`
   - Ensure at least one `reason` explicitly references each hard violation.

2. **If `hard_violations` is empty and `borderline_flags` is empty → candidate is a “clean pass”:**

   - If `score` is reasonably high (for example, `score >= 70`) → `verdict = "ACCEPT"`.
   - If `score` is lower (due to whatever scoring scheme you have) but there are still no hard violations or borderline flags, you may still set `verdict = "ACCEPT"`; the key point is: no rule-based reason to downgrade.

3. **If `hard_violations` is empty and `borderline_flags` is non-empty → candidate is genuinely borderline:**

   - `verdict = "BORDERLINE"`.
   - At least one `reason` should clearly say which metrics are borderline relative to their thresholds, with explicit numeric values.

4. **Regime flags must NOT automatically force BORDERLINE for otherwise strong candidates**

   - `regime_flags` are **informational** unless you explicitly want regime consistency to be a hard rule.
   - They should be appended to `reasons`, but should not by themselves push a strong candidate into `BORDERLINE`.

   **Important:**  

   - If the *only* issues are regime flags (no hard violations, no borderline flags), then:
     - `verdict` should still be `"ACCEPT"` for a strong candidate (e.g. score ≥ 70).
     - The rationale and reasons should mention that regime context is missing, but that the idea passes all configured numeric rules.

5. **Tie-breaking with score for mixed cases**

   If you have both:

   - Non-empty `borderline_flags`, and
   - Non-empty `regime_flags`,

   then:

   - If `score` is very high (e.g. ≥ 80) and the borderline flags are mild (e.g. only one metric slightly above floor), you may still choose `"ACCEPT"` if that aligns with your scoring semantics, but the simpler and safer approach is:

     - `verdict = "BORDERLINE"` whenever `borderline_flags` is non-empty, regardless of `regime_flags`.

   The key requirement is: **regime flags alone should not downgrade a candidate; borderline flags are the primary driver of BORDERLINE, hard_violations drive REJECT.**

---

## 3. Rationale and Reasons – Make Them Match the Verdict

Update how `rationale` and `reasons` are built so they clearly align with the new verdict rules:

### 3.1. For `ACCEPT`

- Rationale should emphasise that the candidate passes key human rules:

  - Example:

    > `"GOOGL short_put_spread_index_45d: DTE 45 in [40, 50], width 5.00, short Δ 0.35 within [0.25, 0.35], IVR 0.27 > 0.25 floor, POP 0.66 > 0.60 floor, cpw 0.30 > 0.25 floor – ACCEPT (regime data missing)."`

- `reasons` may include regime flags, but they should be clearly labelled as caution/notes, e.g.:

  - `"trend_regime missing while allowlist configured – treat regime context with caution."`

### 3.2. For `BORDERLINE`

- Rationale should mention which metrics are borderline, not just that they pass:

  - Example:

    > `"ADBE short_put_spread_index_45d: metrics pass floors but IVR 0.26 and POP 0.61 are only just above their floors – BORDERLINE."`

- `reasons` should include:

  - `"IVR 0.26 is only slightly above floor 0.25 – borderline."`
  - `"POP 0.61 is only slightly above floor 0.60 – borderline."`

### 3.3. For `REJECT`

- Rationale should explicitly state the failed rules, e.g.:

  - `"SPY short_put_spread_index_45d: IVR 0.18 < floor 0.25 and credit/width 0.20 < floor 0.25 – REJECT."`

- `reasons` should contain one entry per violation.

---

## 4. Tests – Adjust and Add

Update or extend the existing tests in the vetting test modules to reflect the new behaviour. Use the same test files and helpers currently in the branch; do not change filenames or test structure arbitrarily.

### 4.1. Strong candidate with missing regimes → ACCEPT

Find or create a test that resembles the current GOOGL case. It should construct or retrieve vetting inputs where:

- DTE is within window.
- Spread width matches expected.
- Short delta within band.
- IVR, POP, credit/width comfortably above floors.
- `trend_regime` and `vol_regime` are missing or `None`.
- Score is high (e.g. ≥ 80).

Assertions:

- `verdict == "ACCEPT"` (or the equivalent enum value).
- `rationale` contains `"ACCEPT"`.
- `reasons` contains at least one regime-related message, but this message did not force a BORDERLINE verdict.

### 4.2. Genuine borderline candidate → BORDERLINE

Test case where:

- DTE is at the edge of [min, max] or
- IVR/POP/cpw are only just above their floors (according to whatever “borderline” thresholds the code uses).

Assertions:

- `verdict == "BORDERLINE"`.
- At least one `reason` explicitly mentions “borderline” (or the equivalent phrase used in the implementation) and includes numeric comparisons to the floor/band.

### 4.3. Hard violation → REJECT

Test case where at least one metric definitively violates a rule, e.g.:

- IVR < floor, OR
- POP < floor, OR
- credit/width < floor, OR
- DTE outside allowed [min, max].

Assertions:

- `verdict == "REJECT"`.
- At least one `reason` clearly states which metric failed and the relevant floor/window.

### 4.4. CLI smoke test remains valid

The existing CLI tests for `ideas-vet` should still pass, but you may want to add one assertion to the JSON-mode test to ensure:

- The `verdict` field is present inside `vetting`.
- For a known strong candidate fixture, the JSON `verdict` now reads `"ACCEPT"`.

---

## 5. Self-Check Before Commit

From the `feature/ideas-vet-view` branch:

1. Run the test suite:

   ```bash
   pytest -q
   ```

2. Run a quick manual smoke test in mock mode:

   ```bash
   export STRATDECK_DATA_MODE=mock

   python -m stratdeck.cli trade-ideas \
     --universe index_core \
     --strategy short_put_spread_index_45d \
     --json-output > .stratdeck/last_trade_ideas.json

   python -m stratdeck.cli ideas-vet
   python -m stratdeck.cli ideas-vet --json-output > /tmp/vetted_ideas.json

   jq '.[0] | {symbol, strategy, vetting}' /tmp/vetted_ideas.json
   ```

3. Confirm that:

   - Strong candidates like the GOOGL example now show `verdict: "ACCEPT"` with regime issues appearing only as informational notes in `reasons`.
   - Borderline and rejecting behaviour still make sense given the human rules.

Once satisfied, commit the changes and push to `feature/ideas-vet-view`, then update the PR description to mention that verdict classification has been refined so strong but regime-missing trades are ACCEPT rather than BORDERLINE.
