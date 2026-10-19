# Daily cloud content

## Laptop fallback at 09:30

On your existing Mac or Windows checkout, with the working `.env`, credentials
and AI models already configured, install the daily local task:

```bash
# macOS
.venv/bin/python scripts/local_daily_content.py --install
```

```powershell
# Windows
.\.venv\Scripts\python.exe scripts\local_daily_content.py --install
```

Set the laptop's timezone to Amsterdam. The installer registers launchd on Mac
or Task Scheduler on Windows for 09:30; it does not start an immediate upload.
This is a direct local Python task and does not require a Codex session. Your
laptop must be powered on and logged in; keep it awake during generation.
macOS may run a missed trigger after waking from sleep. Windows uses an
interactive task and does not run while logged out. Disable older logon/Shorts
tasks to avoid duplicate batches from those separate workflows.

The runner reads your existing `.env`, refreshes your local YouTube token,
starts the installed Ollama if necessary and schedules tomorrow's five videos.
Logs are in `logs/daily-laptop-YYYY-MM-DD.log`. Attempt markers and failure
handling are the same as below. An expired/revoked token still needs a local
`auth-youtube` run. To remove the task:

```bash
launchctl bootout gui/$(id -u)/com.thijszoontjes.youtube-kanaal-daily
rm ~/Library/LaunchAgents/com.thijszoontjes.youtube-kanaal-daily.plist
```

```powershell
schtasks /Delete /TN youtube-kanaal-daily-content /F
```

## Persistent cloud alternative

Run on a persistent Ubuntu VM, independently of your laptop. The temporary
Codex workspace is not a deployed server. Keep the VM running at the trigger
time. This uses systemd, not GitHub Actions: the local AI models, SQLite history,
refreshed OAuth token and cached assets remain on the server between runs.

The timer starts generation at the configured time in `Europe/Amsterdam`,
including daylight saving changes. Uploads take time; they do not all finish
at the trigger time. YouTube publishes tomorrow's four Shorts at 10:00, 13:00,
15:00 and 19:00, and the long video at 17:00. Generation must finish before
those publication times. The date is fixed at the start, even across midnight.

## Prepare the VM

1. Clone `long-term-videos` (with these changes) as an unprivileged Linux user.
2. Install Python 3.11+, `python3-venv`, FFmpeg (with libass), `espeak-ng`,
   `libsndfile1`, `git` and `util-linux`. Create `.venv` and install the project:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e .
   ```

3. Install/start Ollama on the VM and pull the model configured in `.env`
   (the example uses `llama3.2:3b`). Keep Ollama enabled at server startup.
4. Copy your working `.env` and the files referenced by
   `YOUTUBE_CLIENT_SECRET_PATH` and `YOUTUBE_TOKEN_PATH` over SSH/SCP. Correct
   any Mac/Windows absolute paths to server paths. Keep credentials outside
   Git. Protect `.env` and OAuth files with `chmod 600`.
5. Copy/install the voice and subtitle assets required by your settings.
   The example currently uses Kokoro for Shorts and Chatterbox for long videos;
   Chatterbox needs its optional dependencies (`.venv/bin/pip install -e
   '.[chatterbox]'`) and your configured reference audio. Preserve your working
   voice settings rather than assuming the README's older defaults. Configure
   Piper fallback assets and whisper.cpp if your selected paths require them.
6. Set `MOCK_MODE=false`, the real `PEXELS_API_KEY`, and
   `SCHEDULED_TIMEZONE=Europe/Amsterdam`. Run `.venv/bin/python -m youtube_kanaal
   doctor` and resolve missing runtime dependencies.

Authenticate YouTube on your laptop once with `auth-youtube`, then copy the
resulting reusable token to the VM. A Desktop client JSON alone is insufficient.
The wrapper refreshes the existing token without browser interaction before
rendering. Check your Google OAuth project's publishing status: external apps
in Testing can issue refresh tokens that expire after seven days. The VM must
also have sufficient YouTube upload quota and permission to schedule videos.

## Install and inspect

From the repository on the server, choose the desired trigger time explicitly:

```bash
# 09:30 Amsterdam time (morning)
bash scripts/install_daily_content_timer.sh 09:30
# Or 21:30 Amsterdam time (evening)
bash scripts/install_daily_content_timer.sh 21:30
```

The installer uses sudo to install the units but runs generation as the
repository owner. It enables the timer across reboots. Installing does not
start an immediate upload. The installed code stays at your checkout's version;
update it deliberately with a fast-forward pull while no run is active.

```bash
systemctl list-timers youtube-kanaal-daily.timer --no-pager
systemctl status youtube-kanaal-daily.service --no-pager
journalctl -u youtube-kanaal-daily.service --since today
# Optional immediate production run: uploads tomorrow's five videos.
sudo systemctl start youtube-kanaal-daily.service
# Disable future runs:
sudo systemctl disable --now youtube-kanaal-daily.timer
```

Test a real production run and check the five scheduled videos in YouTube
Studio before relying on unattended execution. No cloud server or credentials
are provisioned by this installer.

## Failure handling

Runs cannot overlap. An attempt marker in `data/daily-content/YYYY-MM-DD.started`
prevents another full batch for the same publication date, including after a
partial failure. Success changes it to `.completed`. A failed long-form upload
now produces a nonzero exit instead of silently reporting success.

There are no automatic whole-batch retries: they could duplicate already
uploaded Shorts. Inspect journal logs, run metadata and YouTube Studio first.
Recover only missing uploads using the existing CLI. Remove an attempt marker
only when you have confirmed that rerunning the entire batch is appropriate.
The timer does not catch up missed triggers after VM downtime, and the service
stops after 18 hours. Check VM capacity and actual rendering time during setup.
