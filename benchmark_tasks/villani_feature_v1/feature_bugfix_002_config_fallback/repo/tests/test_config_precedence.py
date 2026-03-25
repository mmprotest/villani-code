import json
from app.config import load_value

def test_env_wins_over_file(tmp_path, monkeypatch):
    p=tmp_path/'cfg.json'; p.write_text(json.dumps({'value':'file'}), encoding='utf-8')
    monkeypatch.setenv('APP_VALUE','env')
    assert load_value(str(p))=='env'

def test_file_value_used_when_env_absent(tmp_path, monkeypatch):
    p=tmp_path/'cfg.json'; p.write_text(json.dumps({'value':'file'}), encoding='utf-8')
    monkeypatch.delenv('APP_VALUE', raising=False)
    assert load_value(str(p))=='file'

def test_default_when_none(tmp_path, monkeypatch):
    p=tmp_path/'cfg.json'; p.write_text('{}', encoding='utf-8')
    monkeypatch.delenv('APP_VALUE', raising=False)
    assert load_value(str(p))=='default'
