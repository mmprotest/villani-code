from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from villani_code.benchmark.models import BenchmarkTask


class BenchmarkAgentContract(BaseModel):
    """Agent-agnostic benchmark contract rendered identically for every adapter."""

    task_id: str
    objective: str
    repo_root: str
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    expected_files: list[str] = Field(default_factory=list)
    allowed_support_files: list[str] = Field(default_factory=list)
    allowed_support_globs: list[str] = Field(default_factory=list)
    completion_requirement: str
    visible_verification_commands: list[str] = Field(default_factory=list)
    time_budget_minutes: int
    expected_artifacts: list[str] = Field(default_factory=list)


def benchmark_contract_from_task(task: BenchmarkTask, repo_root: Path) -> BenchmarkAgentContract:
    metadata = task.metadata
    completion_bits = [
        "make a meaningful patch in allowed scope",
        "stop when done; the harness decides final success from verifier results + diff policy",
    ]
    if task.inspect_only:
        completion_bits = [
            "do not modify files (inspect-only task)",
            "stop when you have completed the requested analysis",
        ]
    return BenchmarkAgentContract(
        task_id=task.id,
        objective=task.prompt,
        repo_root=str(repo_root),
        allowed_paths=list(task.allowlist_paths or task.allowed_paths),
        forbidden_paths=list(task.forbidden_paths),
        expected_files=list(metadata.expected_files),
        allowed_support_files=list(metadata.allowed_support_files),
        allowed_support_globs=list(metadata.allowed_support_globs),
        completion_requirement="; ".join(completion_bits),
        visible_verification_commands=list(task.visible_verification),
        time_budget_minutes=task.max_minutes,
        expected_artifacts=list(task.expected_artifacts),
    )


def render_benchmark_prompt(task: BenchmarkTask, repo_root: Path) -> str:
    contract = benchmark_contract_from_task(task, repo_root)

    def _lines(title: str, values: list[str]) -> list[str]:
        if not values:
            return [f"{title}: none"]
        return [f"{title}:", *[f"- {value}" for value in values]]

    parts: list[str] = [
        "Benchmark task contract (shared across all agents):",
        f"Task ID: {contract.task_id}",
        f"Objective: {contract.objective}",
        f"Repository root: {contract.repo_root}",
        *(_lines("Expected files", contract.expected_files)),
        *(_lines("Allowed paths", contract.allowed_paths)),
        *(_lines("Forbidden paths", contract.forbidden_paths)),
        *(_lines("Allowed support files", contract.allowed_support_files)),
        *(_lines("Allowed support globs", contract.allowed_support_globs)),
        f"Completion requirement: {contract.completion_requirement}",
        *(_lines("Visible verification commands", contract.visible_verification_commands)),
        *(_lines("Required final artifacts", contract.expected_artifacts)),
        f"Time budget: {contract.time_budget_minutes} minute(s)",
        "Do not assume hidden checks; solve robustly.",
    ]
    return "\n".join(parts)


def extract_shared_contract_section(rendered_prompt: str) -> str:
    """Return the shared benchmark contract section from a rendered benchmark prompt."""
    marker = "Benchmark task contract (shared across all agents):"
    idx = rendered_prompt.find(marker)
    return rendered_prompt[idx:].strip() if idx >= 0 else rendered_prompt.strip()
