from datetime import datetime
from unittest.mock import patch
from app.windowing import bucket_for_event

class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromisoformat("2024-05-10T00:10:00+10:00")

def test_local_midnight_cutoff_uses_local_date():
    with patch("app.windowing.datetime", FrozenDateTime):
        assert bucket_for_event("2024-05-09T23:55:00+10:00") == "history"

def test_same_local_day_is_today():
    with patch("app.windowing.datetime", FrozenDateTime):
        assert bucket_for_event("2024-05-10T00:05:00+10:00") == "today"
