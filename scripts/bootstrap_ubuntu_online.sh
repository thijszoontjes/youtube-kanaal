#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if command -v sudo >/dev/null 2>&1; then
  SUDO=sudo
else
  SUDO=
fi

$SUDO apt-get update
$SUDO apt-get install -y \
  git \
  curl \
  ffmpeg \
  cron \
  build-essential \
  cmake \
  pkg-config \
  libsndfile1 \
  python3 \
  python3-venv \
  python3-pip

if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi

cd "$PROJECT_ROOT"
sh "$SCRIPT_DIR/setup_project.sh"
sh "$SCRIPT_DIR/install_runtime_ai_deps.sh"

cat <<'EOF'

Online Ubuntu bootstrap finished.

Next steps:
1. Copy or write your working .env to this VM.
2. Copy these files to the VM, or provide them through ONLINE_* environment variables:
   - data/credentials/client_secret.json
   - data/credentials/youtube_token.json
   - data/voice_samples/en/*.m4a
3. Make sure these model files exist on disk:
   - the Ollama model from OLLAMA_MODEL
   - WHISPER_MODEL_PATH
   - PIPER_VOICE_MODEL_PATH for fallback
4. Run: python -m youtube_kanaal doctor
5. Run: python -m youtube_kanaal scheduled-run
6. Install the daily schedule with:
   python -m youtube_kanaal install-linux-schedule

EOF
