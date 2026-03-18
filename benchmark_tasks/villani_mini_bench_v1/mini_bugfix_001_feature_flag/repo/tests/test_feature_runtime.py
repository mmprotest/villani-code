from app.runtime import should_enable_feature


def test_enabled_flag_takes_effect():
    assert should_enable_feature({'feature_enabled': True}) is True


def test_truthy_string_takes_effect():
    assert should_enable_feature({'feature_enabled': 'true'}) is True


def test_disabled_flag_stays_disabled():
    assert should_enable_feature({'feature_enabled': False}) is False
