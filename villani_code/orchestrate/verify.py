from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.verifier import run_commands


def detect_verify_commands(repo: Path) -> list[str]:
    if (repo / "pytest.ini").exists() or (repo / "pyproject.toml").exists() and (repo / "tests").exists():
        return ["python -m pytest -q"]
    if (repo / "package.json").exists():
        return ["npm test -- --runInBand"]
    if (repo / "Cargo.toml").exists():
        return ["cargo test -q"]
    return ["git diff --stat"]


def run_verification(repo: Path, command: str, timeout_seconds: int, artifact_dir: Path) -> tuple[bool, dict[str, object]]:
    passed, outcomes, _, _, _ = run_commands(
        repo,
        [command],
        timeout_seconds,
        stage="orchestrate",
        artifact_dir=artifact_dir,
    )
    outcome = outcomes[0] if outcomes else None
    payload = {
        "command": command,
        "passed": passed,
        "exit_code": getattr(outcome, "exit_code", None),
        "stdout_artifact": getattr(outcome, "stdout_artifact", None),
        "stderr_artifact": getattr(outcome, "stderr_artifact", None),
        "metadata_artifact": getattr(outcome, "metadata_artifact", None),
    }
    return passed, payload
