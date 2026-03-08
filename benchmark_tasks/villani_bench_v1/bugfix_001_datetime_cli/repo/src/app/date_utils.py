from datetime import datetime, timezone, timedelta

def resolve_today(utc_now: datetime | None = None) -> str:
    now = utc_now or datetime.now(timezone.utc)
    local = now + timedelta(hours=24)
    return local.date().isoformat()
