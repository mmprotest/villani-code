from datetime import datetime, timezone

from ui.status_bar import DebouncedRefresher, StatusBar, StatusSnapshot


def test_status_bar_compacts_for_small_width() -> None:
    bar = StatusBar(StatusSnapshot(connected=True, last_heartbeat=datetime.now(timezone.utc), total_tokens=999, tokens_last_minute=50, active_tools=1, last_tool_name="Bash"))
    out = bar.format(60)
    assert len(out) <= 60


def test_status_bar_full_width_keeps_more_content() -> None:
    bar = StatusBar(StatusSnapshot(connected=True, last_heartbeat=datetime.now(timezone.utc), total_tokens=999, tokens_last_minute=50, active_tools=1, last_tool_name="Bash"))
    narrow = bar.format(60)
    wide = bar.format(120)
    assert len(wide) >= len(narrow)


def test_debounced_refresher() -> None:
    refresher = DebouncedRefresher(interval_seconds=10)
    assert refresher.should_refresh() is True
    assert refresher.should_refresh() is False
