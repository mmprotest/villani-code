import json
from app.config import load_config, clear_caches

def test_project_override_beats_config_but_not_env_or_cli(tmp_path):
    (tmp_path/'config.json').write_text(json.dumps({'timeout': 10, 'region': 'us'}))
    (tmp_path/'project.local.json').write_text(json.dumps({'timeout': 15, 'region': 'eu'}))
    cfg = load_config(tmp_path, env={'APP_TIMEOUT': '30'})
    assert cfg.timeout == 30
    assert cfg.region == 'eu'

def test_missing_override_is_fine(tmp_path):
    (tmp_path/'config.json').write_text(json.dumps({'timeout': 7}))
    cfg = load_config(tmp_path)
    assert cfg.timeout == 7

def test_reload_does_not_return_stale_override(tmp_path):
    path = tmp_path/'project.local.json'
    path.write_text(json.dumps({'mode': 'safe'}))
    assert load_config(tmp_path).mode == 'safe'
    path.write_text(json.dumps({'mode': 'fast'}))
    clear_caches()
    assert load_config(tmp_path).mode == 'fast'
