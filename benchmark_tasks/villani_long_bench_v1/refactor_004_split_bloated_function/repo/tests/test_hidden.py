import app.pipeline as pipeline
from app.fixtures import SAMPLE_RELEASE


def test_summary_builder_delegates_to_helpers(monkeypatch):
    calls = []

    monkeypatch.setattr(pipeline, 'build_header', lambda release: calls.append('header') or 'release=x env=y')
    monkeypatch.setattr(pipeline, 'build_artifact_section', lambda artifacts: calls.append('artifacts') or 'artifacts=a')
    monkeypatch.setattr(pipeline, 'build_warning_section', lambda checks: calls.append('warnings') or 'warnings=b')
    monkeypatch.setattr(pipeline, 'build_notes_section', lambda notes: calls.append('notes') or 'notes=c')
    assert pipeline.build_release_summary(SAMPLE_RELEASE) == 'release=x env=y\nartifacts=a\nwarnings=b\nnotes=c'
    assert calls == ['header', 'artifacts', 'warnings', 'notes']
