#!/usr/bin/env sh
set -eu

echo "Bootstrapping macOS dependencies for youtube-kanaal"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh and rerun this script."
  exit 1
fi

brew update
brew install ffmpeg ollama

if ! brew list whisper-cpp >/dev/null 2>&1; then
  echo "Attempting to install whisper.cpp via Homebrew"
  brew install whisper-cpp || true
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama was not found after installation. Open the Ollama app once and rerun."
fi

if command -v ollama >/dev/null 2>&1; then
  echo "Pulling default Ollama model"
  ollama pull llama3.1:8b-instruct || true
fi

mkdir -p cache/piper cache/whisper cache/xtts data data/credentials data/voice_samples/en logs output

echo "Bootstrap finished."
echo "Still required:"
echo "- Download a Piper voice model and set PIPER_VOICE_MODEL_PATH in .env"
echo "- Optional free voice cloning: run sh scripts/install_xtts_docker.sh and add English memos to data/voice_samples/en"
echo "- Download a whisper.cpp ggml model and set WHISPER_MODEL_PATH in .env"
echo "- Add your PEXELS_API_KEY to .env"
echo "- Place your Google OAuth client JSON at data/credentials/client_secret.json"
