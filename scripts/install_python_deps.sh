#!/usr/bin/env sh
set -eu

if command -v uv >/dev/null 2>&1; then
  uv venv .venv
  uv sync --extra dev
  exit 0
fi

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
