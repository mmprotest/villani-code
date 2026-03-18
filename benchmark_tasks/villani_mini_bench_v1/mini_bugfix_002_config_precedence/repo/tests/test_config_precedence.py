from app.config import resolve_timeout


def test_cli_beats_env_and_defaults():
    assert resolve_timeout({'timeout': 10}, {'APP_TIMEOUT': '20'}, {'timeout': 5}) == 5


def test_env_beats_defaults_when_cli_missing():
    assert resolve_timeout({'timeout': 10}, {'APP_TIMEOUT': '20'}, {}) == 20


def test_defaults_used_last():
    assert resolve_timeout({'timeout': 10}, {}, {}) == 10


def test_fallback_default_when_missing_everywhere():
    assert resolve_timeout({}, {}, {}) == 30
