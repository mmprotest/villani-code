from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.debug_mode import build_debug_config
from villani_code.state import Runner


class _SequenceClient:
    def __init__(self, responses: list[dict]):
        self._responses = responses
        self._idx = 0

    def create_message(self, payload, stream):
        _ = payload, stream
        if self._idx >= len(self._responses):
            return self._responses[-1]
        response = self._responses[self._idx]
        self._idx += 1
        return response


def _seed_repo(repo: Path) -> None:
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")


def _run_dir(debug_root: Path) -> Path:
    run_dirs = sorted(path for path in debug_root.iterdir() if path.is_dir())
    assert len(run_dirs) == 1
    return run_dirs[0]


def _artifact_paths(debug_root: Path) -> tuple[Path, Path]:
    run_dir = _run_dir(debug_root)
    return run_dir / "transcript.full.json", run_dir / "trajectory.json"


def _completed_client(text: str = "done", usage: dict | None = None) -> _SequenceClient:
    response = {"id": "final", "role": "assistant", "content": [{"type": "text", "text": text}]}
    if usage is not None:
        response["usage"] = usage
    return _SequenceClient([response])


def _run_regular(tmp_path: Path, debug_root: Path, debug: str = "trace", **kwargs):
    _seed_repo(tmp_path)
    runner = Runner(
        client=kwargs.pop("client", _completed_client()),
        repo=tmp_path,
        model="model-a",
        provider="provider-a",
        stream=False,
        print_stream=False,
        debug_config=build_debug_config(debug, debug_root),
        **kwargs,
    )
    return runner.run("please answer")


def test_regular_trace_debug_writes_full_transcript_and_atif(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    result = _run_regular(tmp_path, debug_root)

    transcript_path, trajectory_path = _artifact_paths(debug_root)
    assert transcript_path.exists()
    assert trajectory_path.exists()
    full = json.loads(transcript_path.read_text(encoding="utf-8"))
    atif = json.loads(trajectory_path.read_text(encoding="utf-8"))
    assert full["schema_version"] == "villani-debug-transcript-v1"
    assert full["runtime_mode"] == "execution"
    assert full["messages"] == result["messages"]
    assert full["requests"] == result["transcript"]["requests"]
    assert full["responses"] == result["transcript"]["responses"]
    assert atif["schema_version"] == "ATIF-v1.7"


def test_normal_debug_writes_neither_new_artifact(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    _run_regular(tmp_path, debug_root, debug="normal")

    transcript_path, trajectory_path = _artifact_paths(debug_root)
    assert not transcript_path.exists()
    assert not trajectory_path.exists()


def test_debug_off_writes_neither_new_artifact(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    _run_regular(tmp_path, debug_root, debug="off")

    assert not debug_root.exists()


def test_trace_debug_small_model_writes_neither_new_artifact(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    _run_regular(tmp_path, debug_root, small_model=True)

    transcript_path, trajectory_path = _artifact_paths(debug_root)
    assert not transcript_path.exists()
    assert not trajectory_path.exists()


def test_trace_debug_villani_mode_writes_neither_new_artifact(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    _run_regular(tmp_path, debug_root, villani_mode=True)

    transcript_path, trajectory_path = _artifact_paths(debug_root)
    assert not transcript_path.exists()
    assert not trajectory_path.exists()


def test_trace_debug_benchmark_enabled_writes_neither_new_artifact(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    _run_regular(tmp_path, debug_root, benchmark_config=BenchmarkRuntimeConfig(enabled=True, task_id="t1"))

    transcript_path, trajectory_path = _artifact_paths(debug_root)
    assert not transcript_path.exists()
    assert not trajectory_path.exists()


def test_trace_debug_planning_only_writes_neither_new_artifact(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    _seed_repo(tmp_path)
    runner = Runner(
        client=_completed_client(),
        repo=tmp_path,
        model="model-a",
        stream=False,
        print_stream=False,
        debug_config=build_debug_config("trace", debug_root),
    )
    runner._planning_read_only = True
    runner._runtime_mode = "planning"
    runner.run("make a plan")

    transcript_path, trajectory_path = _artifact_paths(debug_root)
    assert not transcript_path.exists()
    assert not trajectory_path.exists()


def test_atif_root_fields_ordered_steps_and_token_metrics(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    client = _SequenceClient(
        [
            {
                "id": "r1",
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            }
        ]
    )
    _run_regular(tmp_path, debug_root, client=client)

    atif = json.loads(_artifact_paths(debug_root)[1].read_text(encoding="utf-8"))
    assert atif["schema_version"] == "ATIF-v1.7"
    assert atif["session_id"] == atif["trajectory_id"]
    assert atif["agent"]["name"] == "villani-code"
    assert atif["agent"]["model_name"] == "model-a"
    assert atif["agent"]["extra"] == {"provider": "provider-a", "mode": "regular"}
    assert [step["type"] for step in atif["steps"]] == ["system", "user", "agent"]
    agent_step = atif["steps"][-1]
    assert agent_step["metrics"]["input_tokens"] == 11
    assert agent_step["metrics"]["output_tokens"] == 7
    assert agent_step["model_calls"][0]["metrics"]["total_tokens"] == 18
    assert atif["final_metrics"]["model_calls"] == 1
    assert atif["final_metrics"]["input_tokens"] == 11
    assert atif["final_metrics"]["output_tokens"] == 7
    assert atif["extra"]["status"] == "completed"
    assert atif["extra"]["transcript_artifact"] == "transcript.full.json"


def test_tool_calls_and_results_are_linked_by_call_id_without_duplicate_cumulative_steps(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    client = _SequenceClient(
        [
            {
                "id": "r1",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "checking"},
                    {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "pyproject.toml"}},
                ],
            },
            {"id": "r2", "role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]
    )
    _run_regular(tmp_path, debug_root, client=client)

    atif = json.loads(_artifact_paths(debug_root)[1].read_text(encoding="utf-8"))
    agent_steps = [step for step in atif["steps"] if step["type"] == "agent"]
    assert len(agent_steps) == 2
    assert len([step for step in atif["steps"] if step["type"] == "user"]) == 1
    assert agent_steps[0]["tool_calls"] == [{"id": "tool-1", "name": "Read", "arguments": {"file_path": "pyproject.toml"}}]
    assert agent_steps[0]["observations"][0]["tool_call_id"] == "tool-1"
    assert "[project]" in agent_steps[0]["observations"][0]["result"]["content"]


def test_redaction_applies_to_both_new_artifacts(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    client = _SequenceClient(
        [
            {
                "id": "r1",
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "pyproject.toml"}}],
            },
            {"id": "r2", "role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]
    )
    _run_regular(tmp_path, debug_root, client=client, redact=True)

    full_text = _artifact_paths(debug_root)[0].read_text(encoding="utf-8")
    atif_text = _artifact_paths(debug_root)[1].read_text(encoding="utf-8")
    assert "[REDACTED_TOOL_RESULT_CONTENT]" in full_text
    assert "[project]" not in full_text
    assert "[REDACTED_TOOL_RESULT_CONTENT]" in atif_text
    assert "[project]" not in atif_text


def test_export_errors_are_non_fatal(monkeypatch, tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"

    def boom(**_kwargs):
        raise RuntimeError("export failed")

    monkeypatch.setattr("villani_code.debug_recorder.build_full_transcript_artifact", boom)
    result = _run_regular(tmp_path, debug_root)

    assert result["response"]["content"][0]["text"] == "done"
    transcript_path, trajectory_path = _artifact_paths(debug_root)
    assert not transcript_path.exists()
    assert not trajectory_path.exists()
    assert "export failed" in (_run_dir(debug_root) / "stderr.log").read_text(encoding="utf-8")


def test_execution_result_unchanged_except_new_files(tmp_path: Path) -> None:
    trace_root = tmp_path / "trace-debug"
    normal_root = tmp_path / "normal-debug"
    trace_repo = tmp_path / "trace-repo"
    normal_repo = tmp_path / "normal-repo"
    trace_repo.mkdir()
    normal_repo.mkdir()

    trace_result = _run_regular(trace_repo, trace_root, debug="trace", client=_completed_client("same"))
    normal_result = _run_regular(normal_repo, normal_root, debug="normal", client=_completed_client("same"))

    assert trace_result["response"] == normal_result["response"]
    assert trace_result["transcript"]["responses"] == normal_result["transcript"]["responses"]
    trace_messages = json.loads(json.dumps(trace_result["messages"]).replace(str(trace_repo), "<repo>"))
    normal_messages = json.loads(json.dumps(normal_result["messages"]).replace(str(normal_repo), "<repo>"))
    assert trace_messages == normal_messages
    assert _artifact_paths(trace_root)[0].exists()
    assert not _artifact_paths(normal_root)[0].exists()
