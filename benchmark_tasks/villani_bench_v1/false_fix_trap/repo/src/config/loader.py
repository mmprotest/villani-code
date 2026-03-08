from .validator import validate_config


def load_config(env_overrides: dict[str, int]) -> dict[str, int]:
    config: dict[str, int] = {}
    if env_overrides.get("port"):
        config["port"] = int(env_overrides["port"])

    validate_config(config)
    return config
