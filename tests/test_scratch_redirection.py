from pathlib import Path

from villani_code.benchmark.policy import filter_meaningful_touched_paths
from villani_code.tools import BashInput, ReadInput, WriteInput, execute_tool


def test_new_root_level_scratch_file_is_redirected(tmp_path: Path) -> None:
    out = execute_tool("Write", WriteInput(file_path="test_fix.py", content="print('x')").model_dump(), tmp_path)
    assert out["is_error"] is False
    assert not (tmp_path / "test_fix.py").exists()
    assert (tmp_path / ".villani_code" / "scratch" / "default" / "test_fix.py").exists()


def test_existing_root_level_scratch_file_is_not_redirected(tmp_path: Path) -> None:
    root_file = tmp_path / "test_fix.py"
    root_file.write_text("old", encoding="utf-8")
    execute_tool("Write", WriteInput(file_path="test_fix.py", content="new").model_dump(), tmp_path)
    assert root_file.read_text(encoding="utf-8") == "new"


def test_existing_directory_paths_are_not_redirected(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    execute_tool("Write", WriteInput(file_path="tests/test_fix.py", content="a").model_dump(), tmp_path)
    execute_tool("Write", WriteInput(file_path="src/test_fix.py", content="b").model_dump(), tmp_path)
    assert (tmp_path / "tests" / "test_fix.py").exists()
    assert (tmp_path / "src" / "test_fix.py").exists()


def test_redirected_scratch_read_resolves_alias(tmp_path: Path) -> None:
    execute_tool("Write", WriteInput(file_path="verify.py", content="ok").model_dump(), tmp_path)
    out = execute_tool("Read", ReadInput(file_path="verify.py").model_dump(), tmp_path)
    assert out["content"] == "ok"


def test_redirected_scratch_exec_keeps_repo_imports(tmp_path: Path) -> None:
    (tmp_path / "pkg.py").write_text("VALUE = 7\n", encoding="utf-8")
    script = "import pkg\nprint(pkg.VALUE)\n"
    execute_tool("Write", WriteInput(file_path="verify.py", content=script).model_dump(), tmp_path)
    out = execute_tool("Bash", BashInput(command="python verify.py").model_dump(), tmp_path)
    assert '"exit_code": 0' in out["content"]
    assert "7" in out["content"]


def test_final_diff_and_patch_summary_filters_runtime_scratch_paths() -> None:
    touched = ["src/app.py", ".villani_code/scratch/default/test_fix.py"]
    meaningful = filter_meaningful_touched_paths(touched)
    assert meaningful == ["src/app.py"]
