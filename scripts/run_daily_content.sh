#!/usr/bin/env bash
set -euo pipefail
umask 077

PROJECT_ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
export SCHEDULED_TIMEZONE=Europe/Amsterdam
mkdir -p data/daily-content
exec 9>data/daily-content/run.lock
flock -n 9 || { echo 'A daily content run is already active.' >&2; exit 1; }

# Fail before rendering if headless OAuth cannot work. Never open a browser here.
"$PYTHON" - <<'PY'
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from youtube_kanaal.config import load_settings
from youtube_kanaal.services.youtube_service import YOUTUBE_UPLOAD_SCOPE

settings = load_settings()
if settings.mock_mode:
    raise SystemExit('Disable MOCK_MODE before starting the production timer.')
if not settings.pexels_api_key:
    raise SystemExit('Configure PEXELS_API_KEY in .env first.')
if not settings.youtube_client_secret_path.is_file() or not settings.youtube_token_path.is_file():
    raise SystemExit('Copy the OAuth client and authenticated YouTube token to this server first.')
credentials = Credentials.from_authorized_user_file(str(settings.youtube_token_path), YOUTUBE_UPLOAD_SCOPE)
if not credentials.refresh_token:
    raise SystemExit('Authenticate YouTube locally with offline access and copy the refresh token.')
if not credentials.valid:
    credentials.refresh(Request())
settings.youtube_token_path.write_text(credentials.to_json(), encoding='utf-8')
settings.youtube_token_path.chmod(0o600)
PY

# Freeze tomorrow before a potentially long render crosses midnight.
TARGET_DATE=$("$PYTHON" -c 'from datetime import datetime,timedelta; from zoneinfo import ZoneInfo; print((datetime.now(ZoneInfo("Europe/Amsterdam")).date()+timedelta(days=1)).isoformat())')
ATTEMPT="data/daily-content/$TARGET_DATE.started"
if [[ -e "$ATTEMPT" || -e "data/daily-content/$TARGET_DATE.completed" ]]; then
    echo "A run for $TARGET_DATE was already started. Inspect logs and YouTube before retrying to avoid duplicate uploads." >&2
    exit 1
fi
date -u +%FT%TZ > "$ATTEMPT"
"$PYTHON" -m youtube_kanaal daily-content --for "$TARGET_DATE" \
    --short-times "10:00,13:00,15:00,19:00" --video-time "17:00"
mv -- "$ATTEMPT" "data/daily-content/$TARGET_DATE.completed"
