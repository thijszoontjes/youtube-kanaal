# Run Online For Free

This branch is set up for the free, persistent hosting path that fits this repo best:

- host the repo on GitHub for source control
- run the pipeline on an Oracle Cloud Always Free Ubuntu VM
- install the daily schedule with `python -m youtube_kanaal install-linux-schedule`

## Recommended Schedule

- `13:00`
- `15:00`
- `19:00`
- timezone: `Europe/Amsterdam`

These values are now the repo defaults in `.env.example`.

## Why Oracle Cloud Always Free

This project is stateful:

- Ollama models stay on disk
- XTTS caches stay on disk
- whisper and Piper model files stay on disk
- run logs and outputs stay on disk

That makes a persistent free Ubuntu VM a much better fit than a stateless CI runner that would keep rebuilding and redownloading models.

## Fast Setup

1. Push this repo to GitHub.
2. Create an Oracle Cloud Always Free Ubuntu VM.
3. Clone the repo on the VM.
4. Run:

```bash
sh scripts/bootstrap_ubuntu_online.sh
```

5. Put your real `.env` on the VM.
6. Put these files on the VM, or inject them with `prepare-online-runtime`:
   - `data/credentials/client_secret.json`
   - `data/credentials/youtube_token.json`
   - `data/voice_samples/en/*.m4a`
7. Make sure your local model files exist:
   - Ollama model from `OLLAMA_MODEL`
   - whisper model at `WHISPER_MODEL_PATH`
   - Piper model at `PIPER_VOICE_MODEL_PATH`
8. Verify:

```bash
python -m youtube_kanaal doctor
python -m youtube_kanaal scheduled-run
```

9. Install the 3 daily uploads:

```bash
python -m youtube_kanaal install-linux-schedule
```

## Optional: Write Runtime Files From Environment Variables

If you do not want to SCP files by hand, export these variables first:

- `ONLINE_YOUTUBE_CLIENT_SECRET_JSON` or `ONLINE_YOUTUBE_CLIENT_SECRET_JSON_B64`
- `ONLINE_YOUTUBE_TOKEN_JSON` or `ONLINE_YOUTUBE_TOKEN_JSON_B64`
- `ONLINE_XTTS_REFERENCE_AUDIO_B64`
- `ONLINE_XTTS_REFERENCE_AUDIO_FILENAME`

Then run:

```bash
python -m youtube_kanaal prepare-online-runtime
```

## Notes

- Keep `XTTS_FALLBACK_TO_PIPER=true` on the server.
- Use a private GitHub repo if you do not want your source public.
- For a headless VM, authenticate YouTube once on your own machine and copy the resulting token JSON to the server.
