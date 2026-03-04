from villani_code.streaming import StreamCoalescer
from villani_code.status_controller import SpinnerTheme, StatusController


def test_coalescer_suppresses_whitespace_spam_until_text():
    c = StreamCoalescer()
    assert c.consume("\n") == ""
    assert c.consume("   ") == ""
    out = c.consume("hello")
    assert out.endswith("hello")


def test_status_controller_can_disable_stdout_rendering():
    s = StatusController(render_to_stdout=False)
    s.start_waiting("Thinking")
    assert s.status_line()
    s.shutdown()


def test_status_controller_start_waiting_keeps_detail_in_status_line():
    controller = StatusController(render_to_stdout=False)
    controller.start_waiting("Using tool", "file: x.py")
    assert "x.py" in controller.status_line()
    controller.shutdown()


def test_status_line_shows_spinner_frame_while_spinning():
    controller = StatusController(render_to_stdout=False)
    theme = SpinnerTheme(["-", "\\"], ["slogan"], ["micro"])
    with controller._lock:
        controller._themes = [theme]
        controller._theme = theme
        controller.current_phase = "Working"
        controller.current_detail = "file: x.py"
        controller._spinning = True
        controller._frame_index = 0
    assert controller.status_line().startswith("[-] Working")
    controller.shutdown()


def test_print_persistent_outputs_line(capsys):
    controller = StatusController(render_to_stdout=True)
    controller.print_persistent("hello persistent")
    out = capsys.readouterr().out
    controller.shutdown()
    assert "hello persistent\n" in out
