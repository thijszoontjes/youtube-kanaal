# youtube-kanaal

Local, terminal-first YouTube Shorts automation for English Shorts in the format `3 facts about X`.

The pipeline is built around a local-first stack:

- Ollama for topic and script generation
- Piper TTS for local voice-over
- whisper.cpp for subtitle timing
- FFmpeg for assembly and subtitle burn-in
- Pexels API for copyright-friendly stock footage
- YouTube Data API with OAuth 2.0 for uploads
- SQLite for run history, dedupe, and retry state

The default behavior is safe and conservative:

- uploads are off unless you pass `--upload`
- privacy defaults to `private`
- outputs are written into the project and copied to `~/Downloads`
- no YouTube password is ever requested or stored

## Features

- One-command Short generation with `python -m youtube_kanaal make-short`
- Batch generation with `python -m youtube_kanaal make-batch --count 3`
- Curated safe topic buckets with duplicate avoidance
- Pydantic validation for topic, script, title, hashtags, and narration length
- Per-run folders with prompts, responses, logs, subtitles, audio, metadata, and final MP4
- SQLite-backed history, dedupe, and retry support
- Rich CLI output and a `doctor` command for setup diagnostics
- OAuth browser flow for YouTube uploads with local token reuse
- Mock-mode smoke pipeline for local testing without live APIs

## Architecture

Pipeline stages:

1. Topic selection
2. Content generation
3. Narration generation
4. Subtitle generation
5. Stock video search/download
6. Asset planning
7. Video rendering
8. Validation
9. Export to project output
10. Copy final MP4 to Downloads
11. Optional YouTube upload
12. Persist run metadata/history

Project structure:

```text
youtube-kanaal/
  .env.example
  .gitignore
  Makefile
  README.md
  pyproject.toml
  scripts/
  tests/
  youtube_kanaal/
    cli.py
    config.py
    db.py
    exceptions.py
    logging_config.py
    pipelines/
    services/
    models/
    utils/
```

## Why These Packages

- `Typer`: clean CLI commands with minimal boilerplate
- `Pydantic`: strict schema validation for LLM outputs and app config
- `httpx`: simple, typed HTTP client for Ollama and Pexels
- `rich`: readable terminal output for status and summaries
- `tenacity`: retries for flaky network or upload steps
- `logging` with rotating handlers: human logs plus structured JSONL logs
- `pytest`: unit, feature, and end-to-end tests
- `sqlite3`: built-in persistent state without extra services

## Installation on macOS

1. Clone the repo.
2. Run the macOS bootstrap script:

```bash
sh scripts/bootstrap_mac.sh
```

3. Install Python dependencies:

```bash
sh scripts/install_python_deps.sh
```

4. Create `.env` and initialize local folders/database:

```bash
sh scripts/setup_project.sh
```

5. Run diagnostics:

```bash
python -m youtube_kanaal doctor
```

## Required External Tools

- Python 3.11+
- FFmpeg
- Ollama
- Piper
- whisper.cpp

You also need local model assets for:

- a Piper voice model, usually an `.onnx` file
- a whisper.cpp model, usually a local `ggml-*.bin` file

The setup scripts create the expected folders, but you still need to place those model files locally and point `.env` to them.

## Environment Variables

Key settings live in `.env`:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1:8b-instruct
PEXELS_API_KEY=
YOUTUBE_CLIENT_SECRET_PATH=./data/credentials/client_secret.json
YOUTUBE_TOKEN_PATH=./data/credentials/youtube_token.json
DEFAULT_PRIVACY_STATUS=private
PIPER_VOICE_MODEL_PATH=./cache/piper/en_US-lessac-medium.onnx
WHISPER_MODEL_PATH=./cache/whisper/ggml-base.en.bin
DOWNLOADS_DIR=~/Downloads
```

Run this once if `.env` does not exist:

```bash
python -m youtube_kanaal init-config
```

## How YouTube OAuth Works

YouTube upload authentication uses OAuth 2.0 only.

1. Create a Google Cloud OAuth desktop app client.
2. Save the downloaded JSON file to `data/credentials/client_secret.json`, or update `YOUTUBE_CLIENT_SECRET_PATH`.
3. Run:

```bash
python -m youtube_kanaal auth-youtube
```

4. A browser opens once.
5. You approve access.
6. A local token file is stored at `YOUTUBE_TOKEN_PATH` and reused on later uploads.

The code never asks for your YouTube password.

## Why Passwords Are Not Used

The YouTube Data API does not require your channel password for app-based uploads. The correct flow is OAuth 2.0:

- your Google account stays with Google
- this app receives a scoped access token
- the token is stored locally and can be revoked later

Secrets in `.env` are things like:

- `PEXELS_API_KEY`
- `YOUTUBE_CLIENT_SECRET_PATH`
- `YOUTUBE_TOKEN_PATH`
- output and tool paths

Your YouTube password should not be in the prompt, the code, or `.env`.

## Generate One Short

```bash
python -m youtube_kanaal make-short
```

This writes artifacts into `output/<run_id>/` and copies the final MP4 to `~/Downloads` by default.

Useful variants:

```bash
python -m youtube_kanaal make-short --upload
python -m youtube_kanaal make-short --topic "axolotls" --bucket animals
python -m youtube_kanaal make-short --debug
```

## Generate and Upload

```bash
python -m youtube_kanaal make-short --upload
```

Uploads default to `private`. That is intentional. Google may apply extra restrictions to newly created or unverified API projects, so keeping the default private is the safest starting point.

If you want a different default later, change:

```dotenv
DEFAULT_PRIVACY_STATUS=unlisted
```

## Batch Generate 3 Shorts

```bash
python -m youtube_kanaal make-batch --count 3
python -m youtube_kanaal make-batch --count 3 --upload
```

The intended safe workflow is generate first, inspect outputs, and upload only when you explicitly opt in.

## Downloads Folder Behavior

By default the final MP4 is saved in two places:

- the per-run project output directory
- your Downloads folder

Default Downloads path:

- macOS: `~/Downloads`

The app handles:

- path expansion
- directory creation
- writability checks
- collision-safe filenames

Override it with:

```dotenv
DOWNLOADS_DIR=/custom/path
```

## CLI Commands

```bash
python -m youtube_kanaal doctor
python -m youtube_kanaal init-config
python -m youtube_kanaal auth-youtube
python -m youtube_kanaal auth-pexels
python -m youtube_kanaal list-history
python -m youtube_kanaal retry-run <run_id>
python -m youtube_kanaal validate-assets
python -m youtube_kanaal test-pipeline
```

## Scheduling

Local scheduling helpers are included.

`launchd` sample:

- `scripts/youtube-kanaal.launchd.plist`

Example approach:

1. Edit the absolute repo path inside the plist.
2. Load it with `launchctl load ~/Library/LaunchAgents/com.thijszoontjes.youtube-kanaal.plist`

You can also use cron, for example:

```cron
0 9 * * * cd /ABSOLUTE/PATH/TO/youtube-kanaal && . .venv/bin/activate && python -m youtube_kanaal make-batch --count 3
```

## Logging and Debugging

Every run gets:

- a unique `run_id`
- a human log at `logs/<run_id>.log`
- a structured log at `logs/<run_id>.jsonl`
- prompts and model outputs saved into the run folder
- metadata JSON and validation results

Use:

```bash
python -m youtube_kanaal make-short --debug
```

If a run fails, the CLI prints:

- what failed
- why it probably failed
- what path to inspect
- how to retry

## Troubleshooting

- `doctor` fails on Ollama:
  Start Ollama and run `ollama pull llama3.1:8b-instruct`.
- `doctor` warns about Piper voice:
  Download a local Piper `.onnx` voice model and set `PIPER_VOICE_MODEL_PATH`.
- `doctor` warns about whisper.cpp:
  Download a local whisper model and set `WHISPER_MODEL_PATH`.
- `auth-youtube` fails:
  Check that `client_secret.json` exists and is a desktop OAuth client.
- `make-short` fails on Pexels:
  Verify `PEXELS_API_KEY` and rerun `python -m youtube_kanaal auth-pexels`.

## Testing

The project includes:

- unit tests in `tests/unit`
- feature tests in `tests/feature`
- end-to-end CLI tests in `tests/e2e`

Run everything:

```bash
python -m pytest
```

Run just the feature test:

```bash
python -m pytest tests/feature
```

Run just the end-to-end CLI test:

```bash
python -m pytest tests/e2e
```

Run the built-in smoke pipeline without live services:

```bash
python -m youtube_kanaal test-pipeline
```

## GitHub

This repo is already configured for:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/thijszoontjes/youtube-kanaal
git push -u origin main
```

## Manual Inputs You Still Need To Provide

- `PEXELS_API_KEY`
- Google OAuth desktop app `client_secret.json`

You will also likely need to place local Piper and whisper model files on disk, but those are not secrets.

## Future Improvements

- clip-level semantic matching between each fact and background footage
- better fact verification before rendering
- tasteful transitions and motion templates
- publish scheduling metadata for future-dated uploads
- richer analytics and success-rate dashboards
