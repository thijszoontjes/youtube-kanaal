#!/usr/bin/env sh
set -eu

if command -v uv >/dev/null 2>&1; then
  uv venv .venv
  uv sync --extra dev
  exit 0
fi

if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN=python3.11
elif [ -x /opt/homebrew/opt/python@3.11/bin/python3.11 ]; then
  PYTHON_BIN=/opt/homebrew/opt/python@3.11/bin/python3.11
elif [ -x .venv/bin/python ]; then
  PYTHON_BIN=.venv/bin/python
else
  PYTHON_BIN=python3
fi

"$PYTHON_BIN" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
