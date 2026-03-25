from datetime import datetime, timezone
from app.date_utils import resolve_today

def test_today_not_tomorrow():
    assert resolve_today(datetime(2024,1,1,0,30,tzinfo=timezone.utc))=="2024-01-01"
