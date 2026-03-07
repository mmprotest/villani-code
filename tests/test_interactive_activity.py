import pytest

pytest.importorskip("textual")

from villani_code.tui.assets import spinner_themes
from villani_code.tui.messages import SpinnerState
from villani_code.tui.widgets.spinner import SpinnerWidget


def test_spinner_themes_non_empty_and_stable() -> None:
    themes = spinner_themes()
    assert themes
    assert any("Villanifying the repo" in theme.slogans for theme in themes)
    assert any("<Villani>" in "".join(theme.frames) for theme in themes)


def test_spinner_renders_slash_frame_without_markup_error() -> None:
    spinner = SpinnerWidget()
    spinner._active = True
    spinner._theme = type(spinner._theme)(frames=["/"], slogans=["s"], micros=["m"])
    spinner._label = "Thinking"

    assert "[/] Thinking" in spinner._render_text().plain


def test_spinner_state_message_allows_theme_slogans() -> None:
    state = SpinnerState(True, None)
    assert state.label is None
