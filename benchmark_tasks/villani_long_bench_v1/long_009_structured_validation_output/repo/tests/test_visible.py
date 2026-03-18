from app.cli import main

def test_text_mode_unchanged():
    code, text = main(3)
    assert code == 0
    assert text == 'OK'
