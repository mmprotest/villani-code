from __future__ import annotations

import subprocess
from pathlib import Path

from villani_code.autonomy import FindingCategory, Opportunity, TakeoverPlanner, VerificationEngine, VerificationFinding, VerificationStatus
from villani_code.autonomous import VillaniModeController
from villani_code.repo_rules import classify_repo_path, is_authoritative_doc_path, is_ignored_repo_path
from villani_code.state import Runner


class DummyClient:
    def create_message(self, _payload, stream):
        return {"content": [{"type": "text", "text": "ok"}]}


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)


def test_checkpoint_doc_is_not_considered_authoritative() -> None:
    path = ".ipynb_checkpoints/readme-checkpoint.md"
    assert is_authoritative_doc_path(path) is False
    assert is_ignored_repo_path(path) is True
    assert classify_repo_path(path) == "runtime_artifact"


def test_docs_drift_ignores_checkpoint_files(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("Villani docs\n", encoding="utf-8")
    (tmp_path / ".ipynb_checkpoints").mkdir()
    (tmp_path / ".ipynb_checkpoints" / "readme-checkpoint.md").write_text("different\n", encoding="utf-8")
    (tmp_path / "villani_code").mkdir()
    (tmp_path / "villani_code" / "__init__.py").write_text("", encoding="utf-8")

    planner = TakeoverPlanner(tmp_path)
    opportunities = planner.discover_opportunities()
    assert not any(o.category == "stale_docs" for o in opportunities)


def test_low_authority_paths_are_blocked_from_mutation(tmp_path: Path) -> None:
    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, villani_mode=True)
    events: list[dict] = []
    runner.event_callback = events.append

    result = runner._execute_tool_with_policy(
        "Write",
        {"file_path": ".ipynb_checkpoints/foo.md", "content": "x", "mkdirs": True},
        "1",
        0,
    )
    assert result["is_error"] is True
    assert "Skipped low-authority path" in str(result["content"])
    assert any("Skipped low-authority path" in str(e.get("phase", "")) for e in events)


def test_changed_files_split_intentional_and_incidental(tmp_path: Path) -> None:
    controller = VillaniModeController(runner=object(), repo=tmp_path)
    intentional, incidental, _all = controller._split_changes([
        "villani_code/app.py",
        "__pycache__/x.pyc",
        ".villani_code/logs/commands.log",
    ])
    assert "villani_code/app.py" in intentional
    assert "__pycache__/x.pyc" in incidental
    assert ".villani_code/logs/commands.log" in incidental
    assert len(intentional) == 1


def test_blast_radius_ignores_incidental_artifacts(tmp_path: Path) -> None:
    controller = VillaniModeController(runner=object(), repo=tmp_path)
    intentional, incidental, _all = controller._split_changes([
        "__pycache__/x.pyc",
        ".villani_code/index/index.json",
    ])
    assert intentional == []
    assert incidental
    assert len(intentional) == 0


def test_verifier_does_not_claim_missing_file_when_file_exists(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "a.py"
    target.write_text("print('before')\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    before = target.read_text(encoding="utf-8")
    target.write_text("print('after')\n", encoding="utf-8")

    verifier = VerificationEngine(tmp_path)
    findings = [VerificationFinding(FindingCategory.INCOMPLETE_EDIT, "Changed file is missing after edit", "a.py", "high")]
    reconciled = verifier._reconcile_findings(findings, ["a.py"], {"a.py": before}, {"a.py"})
    assert reconciled == []


def test_verifier_promotes_success_when_failures_are_contradicted(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "a.py"
    target.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    before = target.read_text(encoding="utf-8")
    target.write_text("after\n", encoding="utf-8")

    verifier = VerificationEngine(tmp_path)
    result = verifier.verify(
        "edit",
        ["a.py"],
        [],
        intended_targets=["a.py"],
        before_contents={"a.py": before},
    )
    assert result.status == VerificationStatus.PASS


def test_planner_discards_junk_opportunities_before_ranking(tmp_path: Path) -> None:
    planner = TakeoverPlanner(tmp_path)
    junk = Opportunity("junk", "stale_docs", 0.99, 0.99, [".ipynb_checkpoints/readme-checkpoint.md"], "x", "small", "x")
    real = Opportunity("real", "broken_tests", 0.8, 0.8, ["tests/"], "x", "small", "x")
    ranked = [o for o in [junk, real] if planner._is_authoritative_opportunity(o)]
    assert ranked == [real]


def test_verification_avoids_ignored_paths(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "a.py").write_text("print('x')\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_bytes(b"x")

    verifier = VerificationEngine(tmp_path)
    result = verifier.verify("edit", ["__pycache__/a.pyc", "a.py"], [])
    assert "__pycache__/a.pyc" not in result.files_examined
