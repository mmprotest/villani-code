from app.loader import load_settings

def test_cli_profile_value_beats_env_and_file():
    config = {"mode": "safe", "profiles": {"prod": {"mode": "strict"}}}
    s = load_settings(config, env={"APP_MODE": "env"}, cli={"profile": "prod"})
    assert s.mode == "strict"

def test_cli_direct_value_beats_profile_env_and_file():
    config = {"mode": "safe", "profiles": {"prod": {"mode": "strict"}}}
    s = load_settings(config, env={"APP_MODE": "env"}, cli={"profile": "prod", "mode": "cli"})
    assert s.mode == "cli"

def test_retry_cli_still_beats_env():
    s = load_settings({"retries": 2}, env={"APP_RETRIES": "4"}, cli={"retries": "7"})
    assert s.retries == 7
