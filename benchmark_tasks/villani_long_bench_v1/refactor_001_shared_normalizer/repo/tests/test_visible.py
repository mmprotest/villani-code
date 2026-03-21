from app.cli import preview_labels
from app.ingest import prepare_user_record
from app.normalization import normalize_record_name


def test_shared_normalizer_handles_mixed_spacing():
    assert normalize_record_name('  Alpha-Beta__Team ') == 'alpha beta team'


def test_preview_output_stays_the_same():
    assert preview_labels(['  Alpha-Beta ', 'Gamma__Team']) == 'alpha beta,gamma team'
    assert prepare_user_record({'name': ' Beta-Team '})['slug'] == 'beta-team'
