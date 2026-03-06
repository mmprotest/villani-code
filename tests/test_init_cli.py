from pathlib import Path

from typer.testing import CliRunner

from villani_code.cli import app


def test_init_command_creates_project_memory(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / ".villani" / "project_rules.md").exists()
    assert (tmp_path / ".villani" / "validation.json").exists()
