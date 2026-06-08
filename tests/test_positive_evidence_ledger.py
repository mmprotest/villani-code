from __future__ import annotations

import json
from pathlib import Path

from villani_code.debug_mode import DebugConfig, DebugMode
from villani_code.debug_recorder import DebugRecorder
from villani_code.positive_evidence import (
    MAX_POSITIVE_EVIDENCE_WARNINGS,
    POSITIVE_EVIDENCE_WARNING_PREFIX,
    PositiveEvidenceLedger,
)


def _ledger(tmp_path: Path) -> PositiveEvidenceLedger:
    return PositiveEvidenceLedger(tmp_path)


def _observe(
    ledger: PositiveEvidenceLedger,
    tool_name: str,
    tool_input: dict[str, object],
    content: str,
    *,
    turn: int = 1,
) -> dict[str, object]:
    return ledger.observe_tool_result(tool_name, tool_input, content, turn=turn, tool_use_id=f"tool-{turn}")


def test_grep_match_creates_positive_evidence(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    _observe(ledger, "Bash", {"command": "rg matched src"}, "src/a.txt:12:matched text")

    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    assert entry.path == "src/a.txt"
    assert entry.matched_snippet == "matched text"
    assert entry.status == "unresolved"


def test_grep_path_snippet_format_works(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    _observe(ledger, "Bash", {"command": "grep -R matched src"}, "src/a.txt:matched text")

    assert [(entry.path, entry.matched_snippet) for entry in ledger.entries] == [
        ("src/a.txt", "matched text")
    ]


def test_find_output_does_not_create_evidence(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    _observe(ledger, "Bash", {"command": "find . -type f"}, "./src/a.txt\n./src/b.txt")

    assert ledger.entries == []


def test_glob_and_list_output_do_not_create_evidence(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    _observe(ledger, "Glob", {"pattern": "src/**/*.txt"}, "src/a.txt\nsrc/b.txt")
    _observe(ledger, "Ls", {"path": "src"}, "a.txt\nb.txt")

    assert ledger.entries == []


def test_read_resolves_by_inspection_only_when_dismissed_with_evidence(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _observe(ledger, "Bash", {"command": "rg matched src"}, "src/a.txt:12:matched text")

    _observe(ledger, "Read", {"file_path": "src/a.txt"}, "full file contents", turn=2)

    assert ledger.entries[0].status == "inspected"

    assert ledger.observe_agent_text(
        "src/a.txt is benign because the inspected line is part of expected sample data",
        3,
    ) == 1
    assert ledger.entries[0].status == "dismissed_with_evidence"


def test_modification_resolves(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _observe(ledger, "Bash", {"command": "rg matched src"}, "src/a.txt:12:matched text")

    _observe(ledger, "Write", {"file_path": "src/a.txt"}, "updated", turn=2)

    assert ledger.entries[0].status == "modified"


def test_weakened_validation_cannot_clear(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _observe(ledger, "Bash", {"command": "rg matched src"}, "src/a.txt:12:matched text")

    observation = _observe(
        ledger,
        "Bash",
        {"command": "rg matched src | grep -v a.txt | head"},
        "",
        turn=2,
    )

    assert observation["weakened_clear_attempt"] is True
    assert ledger.entries[0].status == "unresolved"
    assert "weakened" in ledger.entries[0].status_history[-1]["reason"]


def test_equal_or_broader_validation_can_clear(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _observe(ledger, "Bash", {"command": "rg matched src"}, "src/a.txt:12:matched text")

    _observe(ledger, "Bash", {"command": "rg matched ."}, "", turn=2)

    assert ledger.entries[0].status == "cleared"


def test_warning_shows_high_signal_unresolved_entries_only(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _observe(ledger, "Bash", {"command": "find . -type f"}, "src/plain.txt\nsrc/listed.txt")
    for index in range(7):
        _observe(ledger, "Bash", {"command": "grep -l matched src"}, f"src/file_{index}.txt", turn=index + 1)
    for index in range(3):
        _observe(
            ledger,
            "Bash",
            {"command": "rg matched src"},
            f"src/high_{index}.txt:12:matched text {index}",
            turn=10 + index,
        )
        _observe(
            ledger,
            "Bash",
            {"command": "rg matched src"},
            f"src/high_{index}.txt:12:matched text {index}",
            turn=20 + index,
        )

    warning = ledger.render_warning(current_turn=30, reason="test")
    warning_lines = [line for line in warning.splitlines() if line.startswith("- ")]

    assert warning.startswith(POSITIVE_EVIDENCE_WARNING_PREFIX)
    assert len(warning_lines) == MAX_POSITIVE_EVIDENCE_WARNINGS
    assert "src/plain.txt" not in warning
    assert "src/listed.txt" not in warning
    assert "src/high_0.txt" in warning
    assert "matched text 0" in warning


def test_artifacts_written(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _observe(ledger, "Bash", {"command": "rg matched src"}, "src/a.txt:12:matched text")
    ledger.render_warning(current_turn=2, reason="test")
    recorder = DebugRecorder(
        DebugConfig(mode=DebugMode.TRACE, debug_root=tmp_path / "debug"),
        "run-positive-evidence",
        "generic objective",
        tmp_path,
        "execution",
        "test-model",
    )
    recorder.write_positive_evidence(ledger.to_dict(), ledger.warning_events)
    ledger_path = recorder.artifacts.path("positive_evidence_ledger.json")
    warnings_path = recorder.artifacts.path("positive_evidence_warnings.jsonl")

    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    warning_rows = [json.loads(line) for line in warnings_path.read_text(encoding="utf-8").splitlines()]
    assert payload["entries"][0]["path"] == "src/a.txt"
    assert warning_rows[0]["reason"] == "test"
