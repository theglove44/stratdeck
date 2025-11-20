#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <task-name>" >&2
  echo "Looks for dev/codex/<task-name>.md" >&2
  exit 1
fi

TASK_NAME="$1"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASK_FILE="$REPO_ROOT/dev/codex/${TASK_NAME}.md"

if [ ! -f "$TASK_FILE" ]; then
  echo "Task file not found: $TASK_FILE" >&2
  exit 1
fi

cd "$REPO_ROOT"

echo ">>> Running Codex task: $TASK_NAME"
echo ">>> Using prompt from: $TASK_FILE"
echo

codex exec \
  --profile stratdeck \
  --full-auto \
  --sandbox workspace-write \
  "$(cat "$TASK_FILE")"
