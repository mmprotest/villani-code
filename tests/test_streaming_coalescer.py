from villani_code.streaming import StreamCoalescer
from villani_code.status_controller import StatusController


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
