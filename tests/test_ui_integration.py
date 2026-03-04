from pathlib import Path

from textual.widgets import Input

from villani_code.tui.app import VillaniTUI


class DummyRunner:
    model = "demo"
    permissions = None


class FakeController:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_prompt(self, text: str) -> None:
        self.calls.append(text)


def test_tui_constructs_with_runner(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    assert app.runner.model == "demo"


def test_tui_uses_textual_css_file(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    assert app.CSS_PATH == "styles.tcss"


def test_enter_submit_path_calls_controller_without_global_enter_binding(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    app.controller = FakeController()
    submitted = Input.Submitted(Input(id="input"), "hello")

    app.on_input_submitted(submitted)

    assert app.controller.calls == ["hello"]


def test_app_does_not_define_global_approval_bindings(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    assert "BINDINGS" not in app.__class__.__dict__
