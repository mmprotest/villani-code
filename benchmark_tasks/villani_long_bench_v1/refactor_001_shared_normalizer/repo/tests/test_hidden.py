import app.ingest as ingest
import app.normalization as normalization
import app.reporting as reporting


def test_both_callers_use_shared_normalizer(monkeypatch):
    calls = []

    def fake(value: str) -> str:
        calls.append(value)
        return 'normalized'

    monkeypatch.setattr(normalization, 'normalize_record_name', fake)
    assert ingest.prepare_user_record({'name': ' Example '})['name'] == 'normalized'
    assert reporting.collect_report_labels([{'name': ' Example '}]) == ['normalized']
    assert calls == [' Example ', ' Example ']
