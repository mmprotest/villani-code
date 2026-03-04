from pathlib import Path

from villani_code.interactive import InteractiveShell


class DummyRunner:
    model = "demo"


def test_interactive_shell_is_textual_wrapper(tmp_path: Path) -> None:
    shell = InteractiveShell(DummyRunner(), tmp_path)
    assert "villani-fying your terminal" in shell.LAUNCH_BANNER
