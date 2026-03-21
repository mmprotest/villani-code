import app.cli as cli
import app.formatters as formatters
import app.http as http
import app.notifications as notifications
from app.service import make_job_result


def test_http_cli_and_notifications_delegate_to_shared_formatter(monkeypatch):
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
    assert notifications.build_notification(result) == 'notify[ok] patched (1 warnings)'


def test_no_warning_payload_stays_backward_compatible():
    result = make_job_result('sync', 3, [])
    assert http.build_http_payload(result) == {
        'job': 'sync',
        'status': 'ok',
        'warnings': [],
        'seconds': 3,
    }
    assert cli.render_report(result) == 'job=sync status=ok warnings=- seconds=3'
    assert notifications.build_notification(result) == 'notify[ok] sync (0 warnings)'
