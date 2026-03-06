import json
from pathlib import Path

from villani_code.benchmark.adapters.base import AgentRunResult
from villani_code.benchmark.runner import BenchmarkRunner


def test_artifact_persistence_writes_expected_files(tmp_path: Path) -> None:
    runner = BenchmarkRunner(output_dir=tmp_path)
    artifact_dir = tmp_path / "art"
    artifact_dir.mkdir(parents=True)
    result = AgentRunResult(
        agent_name="villani",
        task_id="t1",
        success=True,
        exit_reason="exit:0",
        elapsed_seconds=0.1,
        stdout="hello",
        stderr="",
        changed_files=["a.py"],
        git_diff="diff",
        validation_results=[],
        catastrophic_failure=False,
        tokens_input=None,
        tokens_output=None,
        cost_usd=None,
        raw_artifact_dir=str(artifact_dir),
        skipped=False,
        skip_reason=None,
    )
    scorecard = {
        "task_success": True,
        "validation_success": True,
        "elapsed_seconds": 0.1,
        "changed_files_count": 1,
        "unnecessary_files_touched_count": 0,
        "forbidden_files_touched_count": 0,
        "catastrophic_failure": False,
        "retry_count": 0,
        "tokens_input": None,
        "tokens_output": None,
        "cost_usd": None,
        "skipped": False,
        "composite_score": 99.0,
    }
    runner._persist_artifacts(artifact_dir, result, scorecard)

    assert (artifact_dir / "stdout.txt").read_text(encoding="utf-8") == "hello"
    metadata = json.loads((artifact_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["scorecard"]["task_success"] is True
