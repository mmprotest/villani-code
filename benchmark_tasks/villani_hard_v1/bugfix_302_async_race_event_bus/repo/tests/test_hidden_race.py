from concurrent.futures import ThreadPoolExecutor
from app.stats import EventStats

def test_multiple_event_names_are_all_counted_correctly():
    stats = EventStats()
    jobs = [('a', 120), ('b', 180), ('c', 90)]
    with ThreadPoolExecutor(max_workers=20) as ex:
        for name, count in jobs:
            list(ex.map(lambda _: stats.record(name), range(count)))
    assert stats.count_for('a') == 120
    assert stats.count_for('b') == 180
    assert stats.count_for('c') == 90

def test_repeat_runs_do_not_flake():
    for _ in range(5):
        stats = EventStats()
        with ThreadPoolExecutor(max_workers=12) as ex:
            list(ex.map(lambda _: stats.record('x'), range(200)))
        assert stats.count_for('x') == 200
