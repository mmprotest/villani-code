import app.bundle as bundle
import app.paths as paths
import app.registry as registry
import app.reporting as reporting
import app.scanner as scanner


def test_all_call_sites_delegate_to_shared_path_helper(monkeypatch):
    calls = []

    def fake(root: str, candidate: str) -> str:
        calls.append((root, candidate))
        return f'handled:{candidate}'

    monkeypatch.setattr(paths, 'resolve_workspace_path', fake)
    assert bundle.plan_bundle('/tmp/work', ['a.txt']) == ['handled:a.txt']
    assert scanner.collect_manifest('/tmp/work', ['b.txt']) == 'handled:b.txt'
    assert registry.build_registry('/tmp/work', ['c.txt']) == {'handled:c.txt': 'c.txt'}
    assert reporting.render_report('/tmp/work', ['d.txt']) == 'handled:d.txt'
    assert calls == [
        ('/tmp/work', 'a.txt'),
        ('/tmp/work', 'b.txt'),
        ('/tmp/work', 'c.txt'),
        ('/tmp/work', 'd.txt'),
    ]



def test_canonical_display_format_is_stable_for_absolute_paths(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    nested = workspace / 'nested'
    nested.mkdir()
    report = nested / 'report.txt'
    report.write_text('x', encoding='utf-8')
    assert reporting.render_report(str(workspace), [str(report)]) == 'nested/report.txt'
    assert scanner.collect_manifest(str(workspace), [str(report)]) == 'nested/report.txt'
