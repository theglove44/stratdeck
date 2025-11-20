# Codex Tasks for StratDeck

This folder contains prompt files for Codex-Max.

Each file describes a self-contained development task that Codex can run in
`--full-auto` mode against this repo, obeying the rules in AGENTS.md.

Usage pattern:
- Work from a feature branch (e.g. codex/<task-name>).
- Run: ./scripts/codex-task.sh <task-name>
- Review git diff + pytest before merging to main.
