import pytest

from config.loader import load_config


def test_port_zero_is_valid_and_present() -> None:
    loaded = load_config({"port": 0})
    assert loaded["port"] == 0


def test_port_missing_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="missing required setting: port"):
        load_config({})
