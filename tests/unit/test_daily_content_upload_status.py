from types import SimpleNamespace

import pytest

from youtube_kanaal import cli


@pytest.mark.parametrize('uploaded,dry_run,expected_exit', [
    (False, False, 1), (True, False, 0), (False, True, 0),
])
def test_daily_content_reports_missing_long_upload(
    monkeypatch, cli_runner, configured_env, uploaded, dry_run, expected_exit
):
    monkeypatch.setattr(cli, '_run_scheduled_shorts_batch', lambda **kwargs: [])
    monkeypatch.setattr(cli, '_render_scheduled_uploads_table', lambda **kwargs: None)
    monkeypatch.setattr(cli, '_run_long_pipeline_result', lambda request: SimpleNamespace(uploaded=uploaded))
    monkeypatch.setattr(cli, '_render_long_result', lambda result: None)
    arguments = ['daily-content', '--for', 'tomorrow']
    if dry_run:
        arguments.append('--dry-run')
    result = cli_runner.invoke(cli.app, arguments)
    assert result.exit_code == expected_exit, result.output
    if expected_exit:
        assert 'Daily content incomplete' in result.output
