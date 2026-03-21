from app.cli import render_report
from app.http import build_http_payload
from app.notifications import build_notification
from app.service import make_job_result


warn_result = make_job_result('sync', 12, ['slow-cache', 'needs-retry'])
assert build_http_payload(warn_result) == {
    'job': 'sync',
    'status': 'warn',
    'warnings': ['slow-cache', 'needs-retry'],
    'seconds': 12,
}
assert render_report(warn_result) == 'job=sync status=warn warnings=slow-cache,needs-retry seconds=12'
assert build_notification(warn_result) == 'notify[warn] sync (2 warnings)'

ok_result = make_job_result('sync', 3, [])
assert build_http_payload(ok_result)['status'] == 'ok'
assert render_report(ok_result).endswith('warnings=- seconds=3')
assert build_notification(ok_result) == 'notify[ok] sync (0 warnings)'
