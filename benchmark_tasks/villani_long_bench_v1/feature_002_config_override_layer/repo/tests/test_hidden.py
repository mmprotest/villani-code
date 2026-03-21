import json

from app.config import load_config


def test_missing_override_file_is_ignored(tmp_path):
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps({'timeout': 20, 'retries': 4}), encoding='utf-8')
    assert load_config(config_path=str(config_path), override_path=str(tmp_path / 'missing.json'), env={'APP_TIMEOUT': '30'}) == {
        'region': 'us-east-1',
        'timeout': 30,
        'retries': 4,
    }


def test_override_file_can_replace_multiple_values(tmp_path):
    config_path = tmp_path / 'config.json'
    override_path = tmp_path / 'override.json'
    config_path.write_text(json.dumps({'region': 'eu-west-1', 'timeout': 20, 'retries': 4}), encoding='utf-8')
    override_path.write_text(json.dumps({'region': 'ca-central-1', 'timeout': 99}), encoding='utf-8')
    env = {'APP_REGION': 'ap-south-1', 'APP_TIMEOUT': '30', 'APP_RETRIES': '8'}
    assert load_config(config_path=str(config_path), override_path=str(override_path), env=env) == {
        'region': 'ca-central-1',
        'timeout': 99,
        'retries': 8,
    }
