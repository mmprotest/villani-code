from pathlib import Path

from app.cli import preview_paths
from app.paths import resolve_workspace_path


def test_shared_path_helper_preserves_workspace_relative_paths(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    nested = workspace / 'nested'
    nested.mkdir()
    (nested / 'report.txt').write_text('x', encoding='utf-8')
    assert resolve_workspace_path(str(workspace), './nested/../nested/report.txt') == 'nested/report.txt'


def test_cli_preview_output_stays_the_same(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    nested = workspace / 'nested'
    nested.mkdir()
    (nested / 'report.txt').write_text('x', encoding='utf-8')
    payload = preview_paths(str(workspace), ['./nested/report.txt', './nested/../nested/report.txt'])
    assert payload == {
        'bundle': ['nested/report.txt', 'nested/report.txt'],
        'manifest': 'nested/report.txt|nested/report.txt',
    }
