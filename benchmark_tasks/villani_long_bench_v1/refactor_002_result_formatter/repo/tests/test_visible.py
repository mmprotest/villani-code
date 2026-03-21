from app.cli import render_report
from app.formatters import format_job_result
from app.http import build_http_payload
from app.notifications import build_notification
from app.service import make_job_result


def test_shared_formatter_preserves_warn_payloads():
    result = make_job_result('sync', 12, ['slow-cache', 'needs-retry'])
    assert format_job_result(result) == {
        'job': 'sync',
        'status': 'warn',
        'warnings': ['slow-cache', 'needs-retry'],
        'seconds': 12,
    }


def test_cli_http_and_notification_outputs_stay_the_same():
    result = make_job_result('sync', 12, ['slow-cache', 'needs-retry'])
    assert build_http_payload(result) == {
        'job': 'sync',
        'status': 'warn',
        'warnings': ['slow-cache', 'needs-retry'],
        'seconds': 12,
    }
    assert render_report(result) == 'job=sync status=warn warnings=slow-cache,needs-retry seconds=12'
    assert build_notification(result) == 'notify[warn] sync (2 warnings)'
