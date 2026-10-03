"""Install/run the laptop's daily 09:30 task using its existing environment."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LABEL = 'com.thijszoontjes.youtube-kanaal-daily'


def install() -> None:
    python = ROOT / ('.venv/Scripts/python.exe' if sys.platform == 'win32' else '.venv/bin/python')
    if not python.is_file() or not (ROOT / '.env').is_file():
        raise SystemExit('Set up the existing project .venv and .env first.')
    script = Path(__file__).resolve()
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    if sys.platform == 'darwin':
        target = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'Label': LABEL,
            'ProgramArguments': [str(python), str(script), '--run'],
            'WorkingDirectory': str(ROOT),
            'StartCalendarInterval': {'Hour': 9, 'Minute': 30},
            'StandardOutPath': str(logs / 'daily-laptop.out.log'),
            'StandardErrorPath': str(logs / 'daily-laptop.err.log'),
            'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
                                     'PYTHONUNBUFFERED': '1'},
        }
        with target.open('wb') as handle:
            plistlib.dump(payload, handle)
        domain = f'gui/{os.getuid()}'
        subprocess.run(['launchctl', 'bootout', f'{domain}/{LABEL}'], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['launchctl', 'bootstrap', domain, str(target)], check=True)
        print(f'Installed {target}. Daily at 09:30 in the laptop timezone; no upload started now.')
    elif sys.platform == 'win32':
        command = subprocess.list2cmdline([str(python), str(script), '--run'])
        subprocess.run(['schtasks', '/Create', '/F', '/SC', 'DAILY', '/ST', '09:30',
                        '/TN', 'youtube-kanaal-daily-content', '/TR', command, '/IT'], check=True)
        print('Installed daily task at 09:30 in the laptop timezone, while logged in.')
    else:
        raise SystemExit('Use install_daily_content_timer.sh on a Linux server.')


def run() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ['SCHEDULED_TIMEZONE'] = 'Europe/Amsterdam'
    os.environ['PYTHONUNBUFFERED'] = '1'
    from youtube_kanaal.config import load_settings
    from youtube_kanaal.services.youtube_service import YOUTUBE_UPLOAD_SCOPE
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    import httpx

    settings = load_settings()
    if settings.mock_mode or not settings.pexels_api_key:
        raise SystemExit('Set MOCK_MODE=false and configure PEXELS_API_KEY in the local .env.')
    if not settings.youtube_client_secret_path.is_file() or not settings.youtube_token_path.is_file():
        raise SystemExit('Run auth-youtube locally once before installing unattended uploads.')
    # Validate/refresh without triggering an unattended browser authentication.
    credentials = Credentials.from_authorized_user_file(str(settings.youtube_token_path), YOUTUBE_UPLOAD_SCOPE)
    if not credentials.refresh_token:
        raise SystemExit('YouTube token needs offline access; run auth-youtube locally.')
    if not credentials.valid:
        credentials.refresh(Request())
    settings.youtube_token_path.write_text(credentials.to_json(), encoding='utf-8')

    state = ROOT / 'data/daily-content'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'laptop.lock').open('a+b') as lock:
        if sys.platform == 'win32':
            import msvcrt
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target = (datetime.now(ZoneInfo('Europe/Amsterdam')).date() + timedelta(days=1)).isoformat()
        started = state / f'{target}.started'
        completed = state / f'{target}.completed'
        if started.exists() or completed.exists():
            raise SystemExit(f'A run for {target} already started. Check YouTube and logs before retrying.')

        # Start the installed local Ollama if it is not already serving.
        server = None
        try:
            httpx.get(f'{settings.ollama_base_url}/api/tags', timeout=5).raise_for_status()
        except httpx.HTTPError:
            if settings.ollama_base_url.rstrip('/') not in {'http://127.0.0.1:11434', 'http://localhost:11434'}:
                raise SystemExit('Configured remote Ollama is unavailable.')
            ollama_log = (ROOT / 'logs/ollama-laptop.log').open('a')
            try:
                server = subprocess.Popen(['ollama', 'serve'], stdout=ollama_log, stderr=ollama_log)
            finally:
                ollama_log.close()
            for _ in range(30):
                try:
                    httpx.get(f'{settings.ollama_base_url}/api/tags', timeout=2).raise_for_status()
                    break
                except httpx.HTTPError:
                    time.sleep(1)
            else:
                server.terminate()
                raise SystemExit('Ollama failed to start; inspect logs/ollama-laptop.log.')
        try:
            started.write_text(datetime.now().isoformat(), encoding='utf-8')
            with (ROOT / 'logs' / f'daily-laptop-{target}.log').open('a') as log:
                result = subprocess.run([
                    sys.executable, '-m', 'youtube_kanaal', 'daily-content', '--for', target,
                    '--short-times', '10:00,13:00,15:00,19:00', '--video-time', '17:00',
                ], stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                raise SystemExit(result.returncode)
            started.replace(completed)
        finally:
            if server is not None:
                server.terminate()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--install', action='store_true')
    group.add_argument('--run', action='store_true')
    args = parser.parse_args()
    (ROOT / 'logs').mkdir(exist_ok=True)
    install() if args.install else run()
