from pathlib import Path

from villani_code.orchestrate.orchestrator import orchestrate
from villani_code.orchestrate.state import WorkerReport
from villani_code.orchestrate.worker import WorkerRunResult


def test_orchestrate_creates_artifacts_with_fallback_reports(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("demo\n", encoding="utf-8")

    def fake_worker_runner(*, repo: Path, prompt: str, config):
        return WorkerRunResult(
            returncode=0,
            stdout="WORKER_REPORT_JSON\n{\"status\":\"partial\",\"summary\":\"ok\",\"likely_files\":[\"README.md\"]}",
            stderr="",
            timed_out=False,
            report=WorkerReport(status="partial", summary="ok", likely_files=["README.md"]),
        )

    result = orchestrate(
        task="inspect",
        repo=repo,
        base_url="http://localhost:1",
        model="demo",
        provider="openai",
        api_key=None,
        workers=1,
        scout_workers=1,
        patch_workers=1,
        rounds=1,
        worker_timeout=1,
        verify_command="git status",
        output_dir=tmp_path / "out",
        keep_worktrees=False,
        worker_runner=fake_worker_runner,
    )

    assert "stop_reason" in result
    assert (tmp_path / "out" / "state.json").exists()
    assert (tmp_path / "out" / "final_report.json").exists()
    assert (tmp_path / "out" / "summary.md").exists()
