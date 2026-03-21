import app.bundle as bundle
import app.paths as paths
import app.scanner as scanner


def test_bundle_and_scanner_delegate_to_shared_path_helper(monkeypatch):
    calls = []

    def fake(root: str, candidate: str) -> str:
        calls.append((root, candidate))
        return f'handled:{candidate}'

    monkeypatch.setattr(paths, 'resolve_workspace_path', fake)
    assert bundle.plan_bundle('/tmp/work', ['a.txt']) == ['handled:a.txt']
    assert scanner.collect_manifest('/tmp/work', ['b.txt']) == 'handled:b.txt'
    assert calls == [('/tmp/work', 'a.txt'), ('/tmp/work', 'b.txt')]
