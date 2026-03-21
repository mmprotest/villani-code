import app.cli as cli
import app.formatters as formatters
import app.http as http
from app.service import make_job_result


def test_http_and_cli_delegate_to_shared_formatter(monkeypatch):
    shaped = {
        'job': 'patched',
        'status': 'ok',
        'warnings': ['delegated'],
        'seconds': 99,
    }

    def fake(result):
        return shaped

    monkeypatch.setattr(formatters, 'format_job_result', fake)
    result = make_job_result('ignored', 1, ['x'])
    assert http.build_http_payload(result) == shaped
    assert cli.render_report(result) == 'job=patched status=ok warnings=delegated seconds=99'
