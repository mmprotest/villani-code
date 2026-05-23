from pathlib import Path

import pytest

from villani_code import tools
from villani_code.tools import GitSimpleInput, LsInput, _safe_path, execute_tool


def test_safe_path_accepts_in_repo_path(tmp_path: Path) -> None:
    nested = tmp_path / "dir" / "file.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("x", encoding="utf-8")
    assert _safe_path(tmp_path, "dir/file.txt") == nested.resolve()


def test_safe_path_rejects_sibling_prefix_escape(tmp_path: Path) -> None:
    sibling = tmp_path.parent / f"{tmp_path.name}-evil"
    sibling.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="Path escapes repository"):
        _safe_path(tmp_path, f"../{sibling.name}/loot.txt")


def test_model_defaults_do_not_share_list_state() -> None:
    ls_one = LsInput()
    ls_two = LsInput()
    ls_one.ignore.append("custom")
    assert "custom" not in ls_two.ignore

    git_one = GitSimpleInput()
    git_two = GitSimpleInput()
    git_one.args.append("status")
    assert git_two.args == []


def test_windows_sanitizer_rejects_head_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools.sys, "platform", "win32")
    result = execute_tool("Bash", {"command": "pytest -q 2>&1 | head -80"}, repo=tmp_path)
    assert result["is_error"] is True
    assert "Unix-style shell syntax/tooling" in result["content"]


def test_windows_sanitizer_rejects_powershell_cmdlet_without_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools.sys, "platform", "win32")
    result = execute_tool("Bash", {"command": "pytest -q 2>&1 | Select-Object -First 80"}, repo=tmp_path)
    assert result["is_error"] is True
    assert "PowerShell cmdlet used without explicit PowerShell invocation" in result["content"]


def test_sanitizer_rejects_large_multiline_python_c(tmp_path: Path) -> None:
    source = "print('x')\\n" * 100
    result = execute_tool("Bash", {"command": f'python -c "{source}" > out.py'}, repo=tmp_path)
    assert result["is_error"] is True
    assert "Do not use shell quoting to write source files" in result["content"]


def test_sanitizer_allows_short_python_c(tmp_path: Path) -> None:
    result = execute_tool("Bash", {"command": 'python -c "import sys; print(sys.version)"'}, repo=tmp_path)
    assert result["is_error"] is False


def test_bash_output_truncates_for_model_and_keeps_debug_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tools.subprocess,
        "run",
        lambda *args, **kwargs: type("P", (), {"returncode": 0, "stdout": "a" * 9000, "stderr": ""})(),
    )
    events: list[dict[str, object]] = []
    result = execute_tool(
        "Bash",
        {"command": "echo hi"},
        repo=tmp_path,
        debug_callback=lambda _name, payload: events.append(payload),
        tool_call_id="t1",
    )
    assert result["is_error"] is False
    assert "[stdout truncated to 8000 chars]" in result["content"]
    finished = events[-1]
    assert finished["truncated"] is True
    assert len(str(finished["stdout"])) == 9000
