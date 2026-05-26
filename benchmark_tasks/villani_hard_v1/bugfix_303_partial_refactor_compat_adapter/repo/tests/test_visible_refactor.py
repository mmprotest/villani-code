from app import render_report

def test_new_renderer_keeps_legacy_compact_output_shape():
    assert render_report('Weekly Status', ['alpha', 'beta'], compact=True) == 'Weekly Status | - alpha | - beta'
