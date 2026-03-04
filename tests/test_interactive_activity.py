from villani_code.tui.assets import spinner_themes


def test_spinner_themes_non_empty_and_stable() -> None:
    themes = spinner_themes()
    assert themes
    assert any("Villanifying the repo" in theme.slogans for theme in themes)
    assert any("<Villani>" in "".join(theme.frames) for theme in themes)
