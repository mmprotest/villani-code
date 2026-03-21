from app.cli import preview_paths
from app.paths import resolve_workspace_path
from app.registry import build_registry
from app.reporting import render_report


def test_shared_path_helper_preserves_workspace_relative_paths(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    nested = workspace / 'nested'
    nested.mkdir()
    (nested / 'report.txt').write_text('x', encoding='utf-8')
    absolute = nested / 'report.txt'
    assert resolve_workspace_path(str(workspace), './nested/../nested/report.txt') == 'nested/report.txt'
    assert resolve_workspace_path(str(workspace), str(absolute)) == 'nested/report.txt'



def test_cli_registry_and_reporting_outputs_stay_canonical(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    nested = workspace / 'nested'
    nested.mkdir()
    report = nested / 'report.txt'
    report.write_text('x', encoding='utf-8')
    payload = preview_paths(str(workspace), ['./nested/report.txt', str(report)])
    assert payload == {
        'bundle': ['nested/report.txt', 'nested/report.txt'],
        'manifest': 'nested/report.txt|nested/report.txt',
    }
    assert build_registry(str(workspace), ['./nested/report.txt', str(report)]) == {
        'nested/report.txt': str(report),
    }
    assert render_report(str(workspace), ['./nested/report.txt', str(report)]) == 'nested/report.txt,nested/report.txt'
