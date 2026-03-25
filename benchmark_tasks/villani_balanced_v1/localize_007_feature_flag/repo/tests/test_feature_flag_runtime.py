from app.config import get_flags
from app.runtime import render

def test_enabled_flag_takes_effect():
    assert render(get_flags())=='new'

def test_disabled_defaults_old():
    assert render({})=='old'
