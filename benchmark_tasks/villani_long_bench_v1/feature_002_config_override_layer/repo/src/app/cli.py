from .config import load_config


def describe_runtime(config_path: str | None = None, env: dict[str, str] | None = None) -> str:
    config = load_config(config_path=config_path, env=env)
    return f"region={config['region']} timeout={config['timeout']} retries={config['retries']}"
