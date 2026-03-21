import json

from app.cli import describe_runtime
from app.config import load_config


def test_override_file_has_highest_precedence(tmp_path):
    config_path = tmp_path / 'config.json'
    override_path = tmp_path / 'override.json'
    config_path.write_text(json.dumps({'region': 'eu-west-1', 'timeout': 20, 'retries': 4}), encoding='utf-8')
    override_path.write_text(json.dumps({'timeout': 99}), encoding='utf-8')

    env = {'APP_TIMEOUT': '30', 'APP_REGION': 'ap-south-1'}
    assert load_config(config_path=str(config_path), override_path=str(override_path), env=env) == {
        'region': 'ap-south-1',
        'timeout': 99,
        'retries': 4,
    }


def test_cli_description_uses_override_layer(tmp_path):
    config_path = tmp_path / 'config.json'
    override_path = tmp_path / 'override.json'
    config_path.write_text(json.dumps({'timeout': 20}), encoding='utf-8')
    override_path.write_text(json.dumps({'timeout': 99, 'retries': 5}), encoding='utf-8')
    assert describe_runtime(str(config_path), str(override_path), {'APP_TIMEOUT': '30'}) == 'region=us-east-1 timeout=99 retries=5'
