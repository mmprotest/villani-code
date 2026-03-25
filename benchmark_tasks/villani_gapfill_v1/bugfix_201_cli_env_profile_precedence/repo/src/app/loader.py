from __future__ import annotations
import os
from .settings import Settings

def load_settings(config: dict, env: dict | None = None, cli: dict | None = None) -> Settings:
    env = env or os.environ
    cli = cli or {}
    file_profile = config.get("profiles", {}).get(cli.get("profile"), {})
    mode = config.get("mode", "safe")
    if "mode" in file_profile:
        mode = file_profile["mode"]
    mode = env.get("APP_MODE", mode)
    if "mode" in cli:
        mode = cli["mode"]
    retries = int(config.get("retries", 1))
    retries = int(env.get("APP_RETRIES", retries))
    if "retries" in cli:
        retries = int(cli["retries"])
    return Settings(mode=mode, retries=retries)
