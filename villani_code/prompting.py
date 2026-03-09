from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.utils import now_local_date


def build_system_blocks(repo: Path, repo_map: str = "", villani_mode: bool = False, benchmark_config: BenchmarkRuntimeConfig | None = None) -> list[dict[str, str]]:
    text = (
        "You are an interactive Villani Code agent for software engineering tasks. "
        "Use tools conservatively, verify changes, and keep outputs concise."
    )
    if villani_mode:
        text = (
            "You are Villani mode, a self-directed autonomous repository improvement agent. "
            "Proactively inspect the repo, choose high-value verifiable tasks, execute edits, verify every change, "
            "and continue until no clearly worthwhile work remains or a real blocker is reached. "
            "Do not ask for permission for normal local repo operations, avoid giant speculative rewrites, and report verification honestly."
        )
    benchmark_enabled = bool(benchmark_config and benchmark_config.enabled)
    if benchmark_enabled:
        text = (
            "You are running a bounded benchmark task. Patch only in allowed scope, avoid scratch/helper/exploratory files unless explicitly allowed, "
            "and make the minimal robust fix in real target files. Completion requires at least one actual in-scope code/test patch. "
            "Do not overfit visible checks; prefer fixes that satisfy hidden verification too. If a write is blocked by policy, redirect to the real allowed target file."
        )
        hints: list[str] = []
        if benchmark_config.task_type:
            hints.append(f"task_type={benchmark_config.task_type}")
        if benchmark_config.expected_files:
            hints.append(f"expected_files={benchmark_config.expected_files}")
        hints.append(f"requires_repo_navigation={benchmark_config.requires_repo_navigation}")
        if benchmark_config.likely_tool_sequence:
            hints.append(f"likely_tool_sequence={benchmark_config.likely_tool_sequence}")
        if benchmark_config.visible_verification:
            hints.append(f"visible_verification={benchmark_config.visible_verification}")
        hints.append(f"max_files_touched={benchmark_config.max_files_touched}")
        if benchmark_config.reference_patch_size_lines and benchmark_config.reference_patch_size_lines <= 12:
            hints.append("reference_patch_size=small; keep patch minimal")
        if (benchmark_config.task_type or "").lower() == "single_file_bugfix" and benchmark_config.expected_files:
            hints.append("single_file_bugfix: inspect expected file first, patch quickly, avoid exploration loops, avoid rerunning identical checks without code changes")
        text = f"{text}\n\n<benchmark-task-hints>\n" + "\n".join(f"- {h}" for h in hints) + "\n</benchmark-task-hints>"
    instructions = load_project_instructions(repo)
    blocks = [{"type": "text", "text": text}]
    if instructions:
        blocks.append({"type": "text", "text": f"<project-instructions>\n{instructions}\n</project-instructions>"})
    if repo_map:
        blocks.append({"type": "text", "text": f"<repo-map>\n{repo_map}\n</repo-map>"})
    return blocks


def load_project_instructions(repo: Path) -> str:
    root = repo / "VILLANI.md"
    if not root.exists():
        return ""
    seen: set[Path] = set()

    def load_file(path: Path) -> str:
        if path in seen or not path.exists():
            return ""
        seen.add(path)
        content = path.read_text(encoding="utf-8")
        lines = []
        for line in content.splitlines():
            if line.startswith("@"):
                lines.append(load_file(repo / line[1:].strip()))
            else:
                lines.append(line)
        return "\n".join(lines)

    return load_file(root)


def build_initial_messages(repo: Path, user_instruction: str, autonomous_objective: bool = False) -> list[dict[str, object]]:
    reminders = [
        "<system-reminder>Available tools in Villani Code include filesystem, search, shell, git, web fetch, and editing tools.</system-reminder>",
        f"<system-reminder>Current local date: {now_local_date()}. Repository root: {repo.resolve()}.</system-reminder>",
    ]
    objective_tag = "<autonomous-objective>" if autonomous_objective else "<user-objective>"
    return [{"role": "user", "content": [{"type": "text", "text": r} for r in reminders] + [{"type": "text", "text": f"{objective_tag}{user_instruction}</autonomous-objective>" if autonomous_objective else user_instruction}]}]
