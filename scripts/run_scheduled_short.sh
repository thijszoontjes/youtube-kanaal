#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=${REPO_ROOT:-$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)}

if [ -n "${PYTHON_EXE:-}" ]; then
  PYTHON_BIN=$PYTHON_EXE
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
else
  PYTHON_BIN=python3
fi

cd "$REPO_ROOT"
exec "$PYTHON_BIN" -m youtube_kanaal scheduled-run "$@"
