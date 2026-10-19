#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
RUN_TIME=${1:-09:30}
if [[ ! "$RUN_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
    echo 'Usage: bash scripts/install_daily_content_timer.sh HH:MM' >&2
    exit 1
fi
if [[ "$PROJECT_ROOT" =~ [[:space:]%\"\\] ]]; then
    echo 'Install the repository at a path without whitespace, %, quotes or backslashes.' >&2
    exit 1
fi
if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" || ! -f "$PROJECT_ROOT/.env" ]]; then
    echo 'Set up .venv and .env on the persistent Ubuntu server first.' >&2
    exit 1
fi
if [[ $(id -u) -eq 0 ]]; then
    echo 'Run as the unprivileged repository owner; sudo is used only to install the units.' >&2
    exit 1
fi
TASK_USER=$(id -un)
TEMP_DIR=$(mktemp -d)
trap 'rm -rf -- "$TEMP_DIR"' EXIT
cat > "$TEMP_DIR/youtube-kanaal-daily.service" <<EOF
[Unit]
Description=Generate and schedule four YouTube Shorts and one long video
Wants=network-online.target
After=network-online.target ollama.service

[Service]
Type=oneshot
User=$TASK_USER
WorkingDirectory=$PROJECT_ROOT
ExecStart=/bin/bash $PROJECT_ROOT/scripts/run_daily_content.sh
TimeoutStartSec=18h
UMask=0077
Environment=PYTHONUNBUFFERED=1
Environment=SCHEDULED_TIMEZONE=Europe/Amsterdam
EOF
cat > "$TEMP_DIR/youtube-kanaal-daily.timer" <<EOF
[Unit]
Description=Daily YouTube generation at $RUN_TIME Amsterdam time

[Timer]
OnCalendar=*-*-* $RUN_TIME:00 Europe/Amsterdam
AccuracySec=1s
RandomizedDelaySec=0
Persistent=false
Unit=youtube-kanaal-daily.service

[Install]
WantedBy=timers.target
EOF
systemd-analyze calendar "*-*-* $RUN_TIME:00 Europe/Amsterdam"
sudo install -m 644 "$TEMP_DIR/youtube-kanaal-daily.service" "$TEMP_DIR/youtube-kanaal-daily.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable youtube-kanaal-daily.timer
sudo systemctl restart youtube-kanaal-daily.timer
systemctl list-timers youtube-kanaal-daily.timer --no-pager
