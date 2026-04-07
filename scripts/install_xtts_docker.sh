#!/usr/bin/env sh
set -eu

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for the free XTTS runtime. Install Docker Desktop and rerun this script."
  exit 1
fi

docker pull ghcr.io/coqui-ai/tts-cpu

echo "XTTS CPU image is ready."
echo "Next step: put 1-5 English voice memos in data/voice_samples/en and set NARRATION_ENGINE=xtts in .env"
