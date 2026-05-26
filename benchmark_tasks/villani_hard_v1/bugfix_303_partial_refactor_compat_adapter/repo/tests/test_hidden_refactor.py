from app import render_report
from app.new_api import render_report_v2

def test_legacy_multiline_still_preserves_title_casing():
    assert render_report('Weekly Status', ['alpha'], compact=False).splitlines()[0] == 'Weekly Status'

def test_v2_contract_remains_uppercase():
    assert render_report_v2('Weekly Status', ['alpha'], compact=False).splitlines()[0] == 'WEEKLY STATUS'
