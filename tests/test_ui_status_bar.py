from datetime import datetime, timezone

from ui.status_bar import DebouncedRefresher, StatusBar, StatusSnapshot


def test_status_bar_compacts_for_small_width() -> None:
    bar = StatusBar(StatusSnapshot(connected=True, last_heartbeat=datetime.now(timezone.utc), total_tokens=999, tokens_last_minute=50, active_tools=1, last_tool_name="Bash"))
    out = bar.format(30)
    assert len(out) <= 30


def test_debounced_refresher() -> None:
    refresher = DebouncedRefresher(interval_seconds=10)
    assert refresher.should_refresh() is True
    assert refresher.should_refresh() is False
