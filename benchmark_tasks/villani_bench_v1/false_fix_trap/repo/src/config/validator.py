def validate_config(config: dict) -> None:
    if "port" not in config:
        raise ValueError("missing required setting: port")
