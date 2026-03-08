import json, os

def load_value(config_path: str) -> str:
    file_value = json.loads(open(config_path, encoding='utf-8').read()).get('value')
    env_value = os.environ.get('APP_VALUE')
    return file_value or env_value or 'default'
