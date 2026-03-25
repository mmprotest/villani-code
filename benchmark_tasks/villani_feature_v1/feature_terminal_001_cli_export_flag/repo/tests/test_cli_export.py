from app.cli import main

def test_export_accepts_format_flag(capsys):
    assert main(['export', '--format', 'json']) == 0
    assert capsys.readouterr().out == '{"ok": true}'
