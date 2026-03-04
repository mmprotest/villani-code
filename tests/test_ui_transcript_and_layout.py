from ui.status_bar import StatusBar
from villani_code.interactive import _append_tool_event
from villani_code.tui.app import InputBar, TUIApp, TranscriptView
from villani_code.tui.state import UIState


class DummyOutput:
    def __init__(self, width: int):
        self._width = width

    def get_size(self):
        class S:
            columns = 0

        s = S()
        s.columns = self._width
        return s


def test_transcript_collapses_tool_uses_when_not_verbose() -> None:
    view = TranscriptView(verbose_tool_output=False, tool_burst_limit=5)
    for idx in range(20):
        view.append_tool_call("Bash", {"n": idx})
    view.flush_tool_summary()
    rendered = "".join(part for _style, part in view._get_text())
    assert "+15 more tool uses" in rendered


def test_transcript_keeps_all_tool_uses_when_verbose() -> None:
    view = TranscriptView(verbose_tool_output=True, tool_burst_limit=5)
    for idx in range(20):
        view.append_tool_call("Bash", {"n": idx})
    view.flush_tool_summary()
    rendered = "".join(part for _style, part in view._get_text())
    assert "+" not in rendered


def test_tool_result_is_rendered_once_for_tool_end_and_ignored_for_tool_result() -> None:
    view = TranscriptView()
    status = StatusBar()
    event = {"name": "Bash", "id": "tool-1", "result": {"content": "hello"}}
    _append_tool_event("tool_end", event, view, status)
    _append_tool_event("tool_result", event, view, status)

    tool_results = [item for item in view._items if item.kind == "tool-result"]
    assert len(tool_results) == 1


def test_responsive_panel_uses_single_container() -> None:
    state = UIState(show_tasks=True)
    panel = TranscriptView().window
    app = TUIApp(
        state=state,
        transcript=TranscriptView(),
        input_bar=InputBar(completer=None),
        status_bar=StatusBar(),
        key_bindings=None,
        panel=panel,
        palette_modal=TranscriptView().window,
        help_modal=TranscriptView().window,
        settings_modal=TranscriptView().window,
        output_modal=TranscriptView().window,
        approval_modal=None,
        style=None,
    )
    app.app.output = DummyOutput(80)
    assert app.panel is panel
    assert app.app.layout is not None
