# youtube-kanaal

Local, terminal-first YouTube Shorts automation for English Shorts in the format `3 facts about X`.

## Quick Start

Activate the virtual environment first on macOS/Linux:
.\.venv\Scripts\python -m youtube_kanaal make-short-schedule --date 2026-04-13 --times "13:00,15:00,19:00"

```bash
source .venv/bin/activate
```

If your shell does not provide a `python` command, use `.venv/bin/python` instead.

Run one Short:

```bash
.venv/bin/python -m youtube_kanaal make-short
.venv/bin/python -m youtube_kanaal make-short --upload
.venv/bin/python -m youtube_kanaal scheduled-run
```

Run a batch:

```bash
.venv/bin/python -m youtube_kanaal make-batch --count 3
.venv/bin/python -m youtube_kanaal make-batch --count 3 --upload
```

Run tests:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/feature
.venv/bin/python -m pytest tests/e2e
.venv/bin/python -m youtube_kanaal test-pipeline
```

Install the future Windows auto-upload schedule for `13:00`, `15:00`, and `19:00` local time:

```bash
.venv/bin/python -m youtube_kanaal install-windows-schedule
```

`test-pipeline` is a smoke test. For a playable preview MP4 it still needs FFmpeg installed.

The pipeline is built around a local-first stack:

- Ollama for topic and script generation
- Piper TTS or free XTTS voice cloning for local voice-over
- whisper.cpp for subtitle timing
- FFmpeg for assembly and subtitle burn-in
- Pexels API for copyright-friendly stock footage
- YouTube Data API with OAuth 2.0 for uploads
- SQLite for run history, dedupe, and retry state

The default behavior is safe and conservative:

- uploads are off unless you pass `--upload`
- privacy defaults to `public`
- outputs are written into the project and copied to `~/Downloads`
- no YouTube password is ever requested or stored

For the free online deployment path, see [docs/run-online.md](docs/run-online.md).

## Features

- One-command Short generation with `python -m youtube_kanaal make-short`
- Batch generation with `python -m youtube_kanaal make-batch --count 3`
- Curated safe topic buckets with duplicate avoidance
- Pydantic validation for topic, script, title, hashtags, and narration length
- Per-run folders with prompts, responses, logs, subtitles, audio, metadata, and final MP4
- SQLite-backed history, dedupe, and retry support
- Rich CLI output and a `doctor` command for setup diagnostics
- OAuth browser flow for YouTube uploads with local token reuse
- Free English voice cloning with XTTS from your own voice memos
- Quick voice testing with `python -m youtube_kanaal preview-voice`
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
.venv/bin/python -m youtube_kanaal doctor
```

Optional for free voice cloning with your own English voice:

```bash
sh scripts/install_xtts_docker.sh
```

## Required External Tools

- Python 3.11+
- FFmpeg
- Ollama
- Piper
- whisper.cpp
- Docker Desktop (optional, only for the free XTTS voice-cloning path)

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
DEFAULT_PRIVACY_STATUS=public
NARRATION_ENGINE=piper
PIPER_VOICE_MODEL_PATH=./cache/piper/en_US-john-medium.onnx
XTTS_RUNTIME=docker
XTTS_SPEAKER_WAV_DIR=./data/voice_samples/en
XTTS_LANGUAGE=en
XTTS_FALLBACK_TO_PIPER=true
WHISPER_MODEL_PATH=./cache/whisper/ggml-base.en.bin
DOWNLOADS_DIR=~/Downloads
SCHEDULED_RUN_TIMES=13:00,15:00,19:00
SCHEDULED_TIMEZONE=Europe/Amsterdam
SCHEDULED_TASK_PREFIX=youtube-kanaal-auto-upload
```

Run this once if `.env` does not exist:

```bash
.venv/bin/python -m youtube_kanaal init-config
```

## Free AI Voice From Your Own English Samples

This repo now supports a free local XTTS flow for English voice cloning. It is designed for the workflow where you drop your own voice memos into the repo and let the generated Short script be read in your own voice.

1. Put `1-5` clean English voice memos in `data/voice_samples/en`.
2. iPhone Voice Memos exports such as `.m4a` work fine. The pipeline converts them to WAV automatically with FFmpeg.
3. Set these values in `.env`:

```dotenv
NARRATION_ENGINE=xtts
XTTS_RUNTIME=docker
XTTS_LANGUAGE=en
XTTS_SPEAKER_WAV_DIR=./data/voice_samples/en
XTTS_FALLBACK_TO_PIPER=true
```

4. Pull the free XTTS image once:

```bash
sh scripts/install_xtts_docker.sh
```

5. Preview your cloned voice before rendering a full Short:

```bash
.venv/bin/python -m youtube_kanaal preview-voice "This is a test of my YouTube Shorts voice."
```

6. Generate a Short with your own cloned voice:

```bash
.venv/bin/python -m youtube_kanaal make-short
```

If your voice memos are not there yet, the app now falls back automatically to the original Piper voice so scheduled runs do not fail.

If you want to switch back to the original local voice, set `NARRATION_ENGINE=piper`.

## What You Still Need

For the app itself, the last private credentials you still need are:

- `PEXELS_API_KEY` in `.env`
- Google OAuth desktop client JSON at `data/credentials/client_secret.json` or your configured `YOUTUBE_CLIENT_SECRET_PATH`

You do not need your YouTube password.

You will also still need local tool/model setup for actual production runs:

- FFmpeg installed
- Ollama installed with the configured model pulled
- a local Piper voice model if you keep `NARRATION_ENGINE=piper`
- Docker Desktop plus the XTTS CPU image if you switch to `NARRATION_ENGINE=xtts`
- a local whisper.cpp model

Those are local dependencies, not secrets.

## How To Get `PEXELS_API_KEY`

1. Go to `https://www.pexels.com/api/`
2. Sign in or create a Pexels account.
3. Open the API page and request an API key.
4. Copy the generated key from your Pexels API dashboard.
5. Open your local `.env` file in this repo.
6. Set:

```dotenv
PEXELS_API_KEY=your_real_pexels_key_here
```

Where to put it:

- file: `.env`
- key name: `PEXELS_API_KEY`

How to verify it:

```bash
python -m youtube_kanaal auth-pexels
```

## How To Get Google OAuth `client_secret.json`

This is for YouTube upload. It is not your password.

1. Go to `https://console.cloud.google.com/`
2. Sign in with the Google account that manages your YouTube channel.
3. Create a new Google Cloud project, or select an existing one.
4. In that project, enable the `YouTube Data API v3`.
5. Go to `APIs & Services` -> `OAuth consent screen`.
6. Configure the consent screen. For personal use, `External` is usually fine.
7. Add the app details and save.
8. Go to `APIs & Services` -> `Credentials`.
9. Click `Create Credentials` -> `OAuth client ID`.
10. Choose `Desktop app`.
11. Create it and download the JSON file.
12. Rename it to `client_secret.json` if you want to use the default path.
13. Put the file here in this repo:

```text
data/credentials/client_secret.json
```

Or set a custom path in `.env`:

```dotenv
YOUTUBE_CLIENT_SECRET_PATH=/absolute/path/to/client_secret.json
```

Where to find it later:

- in Google Cloud: `APIs & Services` -> `Credentials`
- locally in this project: `data/credentials/client_secret.json`

How to create the reusable token after that:

```bash
python -m youtube_kanaal auth-youtube
```

What happens next:

- your browser opens once
- you approve access
- the app saves a local token file at `data/credentials/youtube_token.json` or your configured `YOUTUBE_TOKEN_PATH`
- later uploads reuse that token

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

Uploads now default to `public` in this local setup, because that is what the current channel workflow expects.

If you want a different default later, change:

```dotenv
DEFAULT_PRIVACY_STATUS=private
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
python -m youtube_kanaal prepare-online-runtime
python -m youtube_kanaal scheduled-run
python -m youtube_kanaal install-windows-schedule
python -m youtube_kanaal install-linux-schedule
python -m youtube_kanaal list-history
python -m youtube_kanaal retry-run <run_id>
python -m youtube_kanaal validate-assets
python -m youtube_kanaal test-pipeline
```

## Scheduling

Local scheduling helpers are included.

Windows Task Scheduler:

- default local times are `13:00`, `15:00`, and `19:00`
- titles, descriptions, hashtags, and uploads are still generated by the normal pipeline
- uploads stay local and free, using the same OAuth token flow you already set up

Install the three daily Windows tasks:

```bash
python -m youtube_kanaal install-windows-schedule
```

Override the times if needed:

```bash
python -m youtube_kanaal install-windows-schedule --times "13:00,15:00,19:00"
```

The scheduled tasks call:

- `scripts/run_scheduled_short.ps1`
- which runs `python -m youtube_kanaal make-short --upload`

For Linux servers and cloud VMs, use:

- `scripts/run_scheduled_short.sh`
- which runs `python -m youtube_kanaal scheduled-run`

You can also test one scheduled cycle manually:

```bash
python -m youtube_kanaal scheduled-run
```

Settings for later use in `.env`:

```dotenv
SCHEDULED_RUN_TIMES=13:00,15:00,19:00
SCHEDULED_TIMEZONE=Europe/Amsterdam
SCHEDULED_TASK_PREFIX=youtube-kanaal-auto-upload
```

`launchd` sample for macOS is still included:

- `scripts/youtube-kanaal.launchd.plist`

Example approach:

1. Edit the absolute repo path inside the plist.
2. Load it with `launchctl load ~/Library/LaunchAgents/com.thijszoontjes.youtube-kanaal.plist`

You can also use cron, for example:

```cron
0 9 * * * cd /ABSOLUTE/PATH/TO/youtube-kanaal && . .venv/bin/activate && python -m youtube_kanaal make-batch --count 3
```

For a Linux VM that should upload three times a day, a better cron example is:

```cron
0 13 * * * cd /ABSOLUTE/PATH/TO/youtube-kanaal && sh scripts/run_scheduled_short.sh
0 15 * * * cd /ABSOLUTE/PATH/TO/youtube-kanaal && sh scripts/run_scheduled_short.sh
0 19 * * * cd /ABSOLUTE/PATH/TO/youtube-kanaal && sh scripts/run_scheduled_short.sh
```

Or install the Linux schedule automatically with:

```bash
python -m youtube_kanaal install-linux-schedule
```

## Best Free 24/7 Hosting

For this specific repo, the best free hosting option is usually an Oracle Cloud Always Free Ubuntu VM.

Why this is the best fit:

- the VM stays online, so your local models, logs, and downloaded assets remain on disk
- cron works well for exactly `3` daily runs
- this repo is local-model heavy, so stateless runners are a worse fit because they would keep redownloading models

Recommended implementation path:

1. Create an Oracle Cloud Free Tier account and choose your home region carefully.
2. Create an Ubuntu VM in the Always Free tier.
3. SSH into the VM.
4. Install the base dependencies: `git`, `ffmpeg`, `python3`, `python3-venv`, `python3-pip`, `curl`, `build-essential`, and `cmake`.
5. Install Docker and Ollama on the VM.
6. Clone this repo onto the VM and run `sh scripts/setup_project.sh`.
7. Copy your working `.env` values onto the VM.
8. Copy `data/credentials/client_secret.json` and your working `data/credentials/youtube_token.json` from your local machine to the VM.
9. Copy or recreate the local model assets the pipeline needs, especially the Piper voice model and whisper model.
10. If you want to try your own cloned voice on the VM too, copy your English voice memos into `data/voice_samples/en`.
11. Run `python -m youtube_kanaal doctor` on the VM until all required checks pass for the voice path you want to use.
12. Test one real upload manually with `python -m youtube_kanaal scheduled-run`.
13. Install the online schedule with `python -m youtube_kanaal install-linux-schedule`.
14. Push the repo to GitHub if you also want source control and remote backups.

Practical note:

- keep `XTTS_FALLBACK_TO_PIPER=true` on the server, so the uploads still continue with the original AI voice if your own voice samples are missing or not ready there yet
- for a headless VM, the easiest YouTube auth flow is usually to authenticate once on your own machine and then copy the resulting token file to the VM
- if you do not want to copy files by hand, you can export them as environment variables and run `python -m youtube_kanaal prepare-online-runtime`

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
