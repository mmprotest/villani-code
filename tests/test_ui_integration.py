from pathlib import Path

from villani_code.tui.app import VillaniTUI


class DummyRunner:
    model = "demo"
    permissions = None


def test_tui_constructs_with_runner(tmp_path: Path) -> None:
    app = VillaniTUI(DummyRunner(), tmp_path)
    assert app.runner.model == "demo"
