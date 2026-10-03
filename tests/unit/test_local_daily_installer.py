import importlib.util
from pathlib import Path
import plistlib

import pytest


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_local_installer_registers_0930_without_upload(tmp_path, monkeypatch, platform):
    source = Path(__file__).resolve().parents[2] / 'scripts/local_daily_content.py'
    spec = importlib.util.spec_from_file_location('local_daily_content', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / 'project with spaces'
    python = root / ('.venv/Scripts/python.exe' if platform == 'win32' else '.venv/bin/python')
    python.parent.mkdir(parents=True)
    python.touch()
    (root / '.env').touch()
    monkeypatch.setattr(module, 'ROOT', root)
    monkeypatch.setattr(module.sys, 'platform', platform)
    monkeypatch.setattr(module.Path, 'home', lambda: tmp_path)
    calls = []
    monkeypatch.setattr(module.subprocess, 'run', lambda args, **kwargs: calls.append(args))
    module.install()
    if platform == 'darwin':
        payload = plistlib.loads((tmp_path / 'Library/LaunchAgents' / f'{module.LABEL}.plist').read_bytes())
        assert payload['StartCalendarInterval'] == {'Hour': 9, 'Minute': 30}
        assert payload['ProgramArguments'] == [str(python), str(source), '--run']
        assert 'RunAtLoad' not in payload
        assert calls[-1][1] == 'bootstrap'
    else:
        assert calls[0][calls[0].index('/ST') + 1] == '09:30'
        assert '/IT' in calls[0]
        assert str(python) in calls[0][calls[0].index('/TR') + 1]
    assert all('-m' not in call for call in calls)
