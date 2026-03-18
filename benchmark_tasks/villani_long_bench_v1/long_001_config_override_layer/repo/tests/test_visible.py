import json
from app.config import load_config

def test_visible_precedence(tmp_path):
    (tmp_path/'config.json').write_text(json.dumps({'timeout': 10, 'region': 'us'}))
    (tmp_path/'project.local.json').write_text(json.dumps({'timeout': 15}))
    cfg = load_config(tmp_path, cli={'timeout': 40}, env={'APP_TIMEOUT': '30'})
    assert cfg.timeout == 40
