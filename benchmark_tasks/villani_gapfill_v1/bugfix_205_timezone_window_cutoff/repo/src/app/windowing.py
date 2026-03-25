from datetime import datetime, timezone

def bucket_for_event(ts: str) -> str:
    dt = datetime.fromisoformat(ts)
    utc_day = dt.astimezone(timezone.utc).date()
    today = datetime.now(dt.tzinfo).date()
    return "today" if utc_day == today else "history"
