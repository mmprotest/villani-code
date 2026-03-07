from __future__ import annotations

from pathlib import Path
from typing import Callable

from villani_code.autonomous_stop import category_exhaustion_reason
from villani_code.autonomy import Opportunity, TaskContract


def mark_category_discovery(repo: Path, category_state: dict[str, str], is_test_file: Callable[[str], bool]) -> None:
    files = [p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file()]
    if any(is_test_file(f) for f in files):
        category_state["tests"] = "discovered"
    if any(f in {"README.md", "getting-started.md"} or f.startswith("docs/") for f in files):
        category_state["docs"] = "discovered"
    pyproject_has_scripts = False
    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        pyproject_has_scripts = any(token in text for token in ["[project.scripts]", "[tool.poetry.scripts]", "[project.entry-points"])
    if any(f.endswith("cli.py") for f in files) or pyproject_has_scripts:
        category_state["entrypoints"] = "discovered"
    if any(f.endswith(".py") for f in files):
        category_state["imports"] = "discovered"


def update_category_attempt_state(category_state: dict[str, str], task_title: str) -> None:
    title = task_title.lower()
    if "test" in title:
        category_state["tests"] = "attempted"
    if "doc" in title:
        category_state["docs"] = "attempted"
    if "entrypoint" in title or "cli" in title:
        category_state["entrypoints"] = "attempted"
    if "import" in title:
        category_state["imports"] = "attempted"


def surface_followups(category_state: dict[str, str]) -> list[Opportunity]:
    followups: list[Opportunity] = []
    if category_state.get("tests") == "discovered":
        followups.append(
            Opportunity(
                "Run baseline tests",
                "followup_tests",
                0.99,
                0.9,
                ["tests/"],
                "tests remain unexamined",
                "small",
                "run baseline tests",
                TaskContract.VALIDATION.value,
            )
        )
        category_state["tests"] = "attempted"

    if category_state.get("docs") == "discovered":
        followups.append(
            Opportunity(
                "Validate documented commands/examples",
                "followup_docs",
                0.92,
                0.78,
                ["README.md"],
                "docs remain unexamined",
                "small",
                "validate documented commands/examples",
                TaskContract.INSPECTION.value,
            )
        )
        category_state["docs"] = "attempted"

    if category_state.get("entrypoints") == "discovered":
        followups.append(
            Opportunity(
                "Validate CLI entrypoint",
                "followup_entrypoint",
                0.9,
                0.76,
                [],
                "entrypoints remain unexamined",
                "small",
                "validate CLI entrypoint",
                TaskContract.VALIDATION.value,
            )
        )
        category_state["entrypoints"] = "attempted"
    return followups


def stop_reason_from_categories(category_state: dict[str, str]) -> tuple[dict[str, str], str]:
    stop = category_exhaustion_reason(category_state)
    return stop.rationale, stop.done_reason
