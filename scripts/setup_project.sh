#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

cd "$PROJECT_ROOT"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

mkdir -p cache cache/xtts data data/credentials data/voice_samples/en logs output

sh "$SCRIPT_DIR/install_python_deps.sh"

if command -v python >/dev/null 2>&1; then
  python -m youtube_kanaal init-config || true
fi

if command -v ollama >/dev/null 2>&1; then
  ollama pull llama3.1:8b-instruct || true
fi

echo "Project setup completed."
