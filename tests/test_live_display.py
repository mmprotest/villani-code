from villani_code.live_display import apply_live_display_delta


def test_ignores_whitespace_until_first_content() -> None:
    buf, started = apply_live_display_delta("", "\n  \t", False)
    assert buf == ""
    assert started is False

    buf, started = apply_live_display_delta(buf, "hello", started)
    assert buf == "hello"
    assert started is True


def test_caps_consecutive_newlines_to_two() -> None:
    buf, started = apply_live_display_delta("", "hi\n\n\n\nthere", False)
    assert started is True
    assert buf == "hi\n\nthere"


def test_after_started_preserves_text_and_caps_newline_bursts() -> None:
    buf, started = apply_live_display_delta("done\n", "\n\n\nnext", True)
    assert started is True
    assert buf == "done\n\nnext"
