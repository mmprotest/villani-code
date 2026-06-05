from __future__ import annotations

import json
from pathlib import Path

import pytest
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


class _FailingClient:
    def create_message(self, payload, stream):
        _ = payload, stream
        raise RuntimeError("model unavailable")


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


def test_normal_debug_writes_generic_trajectory_only(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    _run_regular(tmp_path, debug_root, debug="normal")

    transcript_path, trajectory_path = _artifact_paths(debug_root)
    assert not transcript_path.exists()
    assert trajectory_path.exists()
    atif = json.loads(trajectory_path.read_text(encoding="utf-8"))
    assert atif["schema_version"] == "ATIF-v1.7"
    assert atif["extra"]["status"] == "completed"


def test_openai_compatible_metadata_uses_configured_inference_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VILLANI_INFERENCE_PROVIDER", "lmstudio")
    debug_root = tmp_path / "debug"
    _seed_repo(tmp_path)
    runner = Runner(
        client=_completed_client(),
        repo=tmp_path,
        model="local-model",
        provider="openai",
        stream=False,
        print_stream=False,
        debug_config=build_debug_config("trace", debug_root),
    )
    runner.run("please answer")

    run_dir = _run_dir(debug_root)
    session_meta = json.loads((run_dir / "session_meta.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "final_summary.json").read_text(encoding="utf-8"))
    transcript = json.loads((run_dir / "transcript.full.json").read_text(encoding="utf-8"))
    trajectory = json.loads((run_dir / "trajectory.json").read_text(encoding="utf-8"))

    expected_model_metadata = {
        "identifier": "local-model",
        "inference_provider": "lmstudio",
        "api_compatibility": "openai",
    }
    assert session_meta["agent"]["name"] == "villani-code"
    assert session_meta["agent"]["version"]
    assert session_meta["model_metadata"] == expected_model_metadata
    assert summary["agent"] == session_meta["agent"]
    assert summary["model_metadata"] == expected_model_metadata
    assert transcript["model_metadata"] == expected_model_metadata
    assert trajectory["agent"]["extra"]["inference_provider"] == "lmstudio"
    assert trajectory["agent"]["extra"]["api_compatibility"] == "openai"


def test_regular_trajectory_exists_after_model_request_failure(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    _seed_repo(tmp_path)
    runner = Runner(
        client=_FailingClient(),
        repo=tmp_path,
        model="local-model",
        provider="openai",
        stream=False,
        print_stream=False,
        debug_config=build_debug_config("normal", debug_root),
    )

    with pytest.raises(RuntimeError, match="model unavailable"):
        runner.run("please answer")

    transcript_path, trajectory_path = _artifact_paths(debug_root)
    assert not transcript_path.exists()
    assert trajectory_path.exists()
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    assert trajectory["schema_version"] == "ATIF-v1.7"
    assert trajectory["extra"]["status"] == "failed"
    assert trajectory["extra"]["termination_reason"] == "model_request_failed"
    assert [step["source"] for step in trajectory["steps"]] == ["system", "user"]


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
    assert atif["agent"]["extra"] == {
        "provider": "provider-a",
        "inference_provider": "provider-a",
        "api_compatibility": None,
        "mode": "regular",
    }
    assert "steps" in atif
    assert [step["step_id"] for step in atif["steps"]] == list(range(1, len(atif["steps"]) + 1))
    assert [step["source"] for step in atif["steps"]] == ["system", "user", "agent"]
    for step in atif["steps"]:
        assert step["source"] in {"system", "user", "agent"}
        assert step["message"] is not None
        assert "type" not in step
        assert "role" not in step
    agent_step = atif["steps"][-1]
    assert agent_step["llm_call_count"] == 1
    assert agent_step["metrics"]["prompt_tokens"] == 11
    assert agent_step["metrics"]["completion_tokens"] == 7
    assert "input_tokens" not in agent_step["metrics"]
    assert "output_tokens" not in agent_step["metrics"]
    assert "total_tokens" not in agent_step["metrics"]
    assert atif["final_metrics"]["total_prompt_tokens"] == 11
    assert atif["final_metrics"]["total_completion_tokens"] == 7
    assert atif["final_metrics"]["total_steps"] == len(atif["steps"])
    assert "input_tokens" not in atif["final_metrics"]
    assert "output_tokens" not in atif["final_metrics"]
    assert "prompt_tokens" not in atif["final_metrics"]
    assert "completion_tokens" not in atif["final_metrics"]
    assert "total_tokens" not in atif["final_metrics"]
    assert atif["extra"]["status"] == "completed"
    assert atif["extra"]["transcript_artifact"] == "transcript.full.json"

    full = json.loads(_artifact_paths(debug_root)[0].read_text(encoding="utf-8"))
    assert full["model"] == "model-a"
    assert full["provider"] == "provider-a"
    assert full["model_metadata"] == {"identifier": "model-a", "inference_provider": "provider-a"}
    assert full["responses"][0]["usage"]["input_tokens"] == 11
    assert full["responses"][0]["usage"]["output_tokens"] == 7
    assert full["responses"][0]["usage"]["total_tokens"] == 18


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
    assert [step["step_id"] for step in atif["steps"]] == list(range(1, len(atif["steps"]) + 1))
    assert all("type" not in step and "role" not in step for step in atif["steps"])
    agent_steps = [step for step in atif["steps"] if step["source"] == "agent"]
    assert len(agent_steps) == 2
    assert len([step for step in atif["steps"] if step["source"] == "user"]) == 1
    assert all(step["llm_call_count"] == 1 for step in agent_steps)
    assert agent_steps[0]["tool_calls"] == [
        {"tool_call_id": "tool-1", "function_name": "Read", "arguments": {"file_path": "pyproject.toml"}}
    ]
    assert "id" not in agent_steps[0]["tool_calls"][0]
    assert "name" not in agent_steps[0]["tool_calls"][0]
    results = agent_steps[0]["observation"]["results"]
    assert results[0]["source_call_id"] == "tool-1"
    assert "[project]" in results[0]["content"]
    assert "result" not in results[0]
    assert "observations" not in agent_steps[0]


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
    atif = json.loads(atif_text)
    first_agent_step = next(step for step in atif["steps"] if step["source"] == "agent")
    assert first_agent_step["message"] == ""
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
    assert trajectory_path.exists()
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
