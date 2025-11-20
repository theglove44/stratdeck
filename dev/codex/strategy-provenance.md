In this repo (StratDeck Copilot), your job is to wire strategy provenance into trade ideas.

CONTEXT:
- Read AGENTS.md and obey all rules there (no live trading, tests must pass, CLI is a public API).
- Trade ideas are produced by the strategy engine and surfaced via:
  python -m stratdeck.cli trade-ideas --strategy ... --universe ... --json-output

GOAL:
- Ensure each trade idea in the JSON output includes a 'provenance' field with:
  - strategy_template_name
  - strategy_template_label (if available)
  - universe_name
  - dte_rule_used (rule + selected)
  - width_rule_used (rule + selected + applied_spread_width)
  - filters_applied:
    - min_pop
    - min_ivr
    - candidate_values: pop, ivr, credit_per_width

REQUIREMENTS:
- Preserve all existing JSON fields; only ADD the 'provenance' block.
- Keep provenance reasonably compact; do NOT dump full chains or quotes.
- Use any existing helpers (e.g. a _dump_model helper) where appropriate.
- Add or update tests under tests/ to assert that provenance is present and minimally correct.
- All tests must pass (run 'python -m pytest') before you consider the task complete.

WORKFLOW:
1. Find where TradeIdea objects are created and how CLI JSON is emitted.
2. Thread provenance information from strategy templates, universes, and filters into TradeIdea.provenance.
3. Add or update tests to assert provenance presence and structure.
4. Run 'python -m pytest' and fix any failures.
5. At the end, print:
   - A short bullet list summarising the changes.
   - The output of 'git diff' for inspection.
