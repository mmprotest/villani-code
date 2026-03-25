from app.template.render import render

def test_conditional_autoescape_applies_when_enabled():
    assert render("<b>x</b>", autoescape=True) == "&lt;b&gt;x&lt;/b&gt;"
    assert render("<b>x</b>", autoescape=False) == "<b>x</b>"

def test_default_autoescape_uses_config():
    assert render("<i>x</i>") == "&lt;i&gt;x&lt;/i&gt;"
