from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.planning import TaskMode
from villani_code.utils import now_local_date


def build_system_blocks(repo: Path, repo_map: str = "", villani_mode: bool = False, benchmark_config: BenchmarkRuntimeConfig | None = None, task_mode: TaskMode | None = None) -> list[dict[str, str]]:
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
    if villani_mode or benchmark_enabled:
        task_hint = f" Task mode: {task_mode.value}." if task_mode else ""
        text = (
            f"{text} Before editing, name one likely target file. Prefer minimal patches over whole-file rewrites. "
            f"Expand scope only with concrete evidence. Verify after meaningful edits. Stop if verification repeats without new evidence.{task_hint}"
        )
    if benchmark_enabled:
        text = (
            f"{text} In benchmark mode, do not create exploratory helper files unless explicitly allowlisted; prefer the real task file over scratch files."
        )
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
