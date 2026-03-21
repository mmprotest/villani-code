from app.cli import render_release
from app.fixtures import SAMPLE_RELEASE
from app.pipeline import build_artifact_section, build_warning_section


def test_release_summary_output_is_preserved():
    assert render_release(SAMPLE_RELEASE) == (
        'release=backend env=prod\n'
        'artifacts=api:ok,worker:warn\n'
        'warnings=smoke:warn\n'
        'notes=deploy after 18:00 UTC'
    )


def test_extracted_helpers_render_existing_sections():
    assert build_artifact_section(SAMPLE_RELEASE['artifacts']) == 'artifacts=api:ok,worker:warn'
    assert build_warning_section(SAMPLE_RELEASE['checks']) == 'warnings=smoke:warn'
