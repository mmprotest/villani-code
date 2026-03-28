from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from villani_code.autonomy import TaskContract
from villani_code.mission import Mission, MissionNode, NodePhase, NodeStatus


def infer_blast_radius(changed_files: list[str], repo_root: str) -> dict[str, Any]:
    radius: dict[str, Any] = {"changed": changed_files, "neighbors": [], "tests": []}
    repo = Path(repo_root)
    for rel in changed_files:
        p = repo / rel
        stem = p.stem
        if not stem:
            continue
        for test in repo.rglob("test*.py"):
            tr = test.relative_to(repo).as_posix()
            if stem in tr:
                radius["tests"].append(tr)
        for file in repo.rglob("*.py"):
            fr = file.relative_to(repo).as_posix()
            if fr != rel and stem in fr:
                radius["neighbors"].append(fr)
    radius["tests"] = sorted(set(radius["tests"]))[:12]
    radius["neighbors"] = sorted(set(radius["neighbors"]))[:20]
    return radius


def infer_candidate_tests(changed_files: list[str], repo_root: str) -> list[str]:
    radius = infer_blast_radius(changed_files, repo_root)
    tests = [f"pytest -q {t}" for t in radius.get("tests", [])[:6]]
    return tests or ["pytest -q"]


def build_change_containment_context(repo_root: str, changed_files: list[str] | None = None) -> dict[str, Any]:
    repo = Path(repo_root)
    changed = list(changed_files or [])
    diff = ""
    if not changed:
        proc = subprocess.run(["git", "diff", "--name-only"], cwd=repo, capture_output=True, text=True)
        if proc.returncode == 0:
            changed = [x.strip() for x in proc.stdout.splitlines() if x.strip()]
    dproc = subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True)
    if dproc.returncode == 0:
        diff = dproc.stdout
    return {
        "changed_files": changed,
        "diff": diff,
        "blast_radius": infer_blast_radius(changed, repo_root),
        "candidate_tests": infer_candidate_tests(changed, repo_root),
    }


def create_regression_containment_nodes(mission: Mission, context: dict[str, Any]) -> list[MissionNode]:
    changed = list(context.get("changed_files", []) or [])
    tests = list(context.get("candidate_tests", []) or [])
    n1 = MissionNode(
        node_id=f"{mission.mission_id}-contain-localize",
        title="Localize diff impact",
        phase=NodePhase.LOCALIZE,
        objective="Inspect changed files and infer blast radius",
        contract_type=TaskContract.CONTAIN_CHANGE.value,
        candidate_files=changed[:20],
        validation_commands=tests[:6],
        status=NodeStatus.READY,
    )
    n2 = MissionNode(
        node_id=f"{mission.mission_id}-contain-validate",
        title="Run containment validation",
        phase=NodePhase.VALIDATE,
        objective="Execute impacted tests and verify no regressions",
        contract_type=TaskContract.CONTAIN_CHANGE.value,
        candidate_files=changed[:20],
        validation_commands=tests[:6],
        depends_on=[n1.node_id],
        status=NodeStatus.PENDING,
    )
    return [n1, n2]
