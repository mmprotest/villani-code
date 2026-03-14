from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.plan_session import PlanSessionResult
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
            f"{text} In benchmark mode, do not create exploratory helper files unless explicitly allowlisted; prefer the real task file over scratch files. Favor narrow, localized edits over broad rewrites unless required by failing evidence."
        )
        if benchmark_config is not None and not benchmark_config.require_patch_artifact:
            text = (
                f"{text} Repro-task emphasis: deliver the required regression test file in allowed scope; source edits are secondary unless explicitly allowed by task scope."
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


def build_planning_instruction(
    user_instruction: str,
    evidence: list[dict[str, str]] | None = None,
    validation_steps: list[str] | None = None,
    answers: list[Any] | None = None,
) -> str:
    evidence = evidence or []
    validation_steps = validation_steps or []
    answers = answers or []
    payload = {
        "instruction": user_instruction,
        "evidence": evidence[:12],
        "validation_steps": validation_steps[:8],
        "resolved_answers": [
            {
                "question_id": getattr(answer, "question_id", ""),
                "selected_option_id": getattr(answer, "selected_option_id", ""),
                "other_text": getattr(answer, "other_text", ""),
            }
            for answer in answers
        ],
    }
    return "\n".join(
        [
            "Create an implementation plan in read-only inspection mode.",
            "Use the normal runtime loop: inspect files, search, reason, and iterate until the plan is concrete.",
            "Do not edit files, do not run mutating commands, and do not perform git mutations.",
            "When the plan is concrete, finalize by calling the SubmitPlan tool.",
            "SubmitPlan input must include: task_summary, candidate_files, assumptions, recommended_steps, open_questions, risk_level, confidence_score.",
            "Clarifying questions are allowed only for true design forks. Each question must include exactly 4 options with exactly one option labeled Other.",
            "Do not return planning JSON in assistant prose; use SubmitPlan for finalization.",
            "Planning context JSON:",
            json.dumps(payload, indent=2),
        ]
    )


def build_execution_instruction_from_plan(plan: PlanSessionResult) -> str:
    answers = []
    for answer in plan.resolved_answers:
        text = f"{answer.question_id}: {answer.selected_option_id}"
        if answer.other_text.strip():
            text += f" (other={answer.other_text.strip()})"
        answers.append(text)
    sections = [
        f"Original instruction: {plan.instruction}",
        f"Approved task summary: {plan.task_summary}",
        "Recommended steps:",
        *[f"- {step}" for step in plan.recommended_steps],
        "Assumptions and constraints:",
        *[f"- {item}" for item in plan.assumptions],
        "Resolved clarifications:",
        *([f"- {item}" for item in answers] if answers else ["- none"]),
        "Execute the approved plan now. Do not re-plan from scratch unless blocked by new evidence.",
    ]
    return "\n".join(sections)


def build_solution_planning_messages(
    instruction: str,
    repo_summary: dict[str, Any],
    evidence: list[dict[str, str]],
    answers: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    system = [
        {
            "type": "text",
            "text": (
                "You are planning work in strict read-only mode. "
                "Fully think through and finalize the best likely execution plan before stopping. "
                "Inspect repository evidence and produce a concrete, repo-specific solution plan. "
                "Avoid generic scaffolding and pre-plans. "
                "Use sensible defaults for broad tasks and ask clarification questions only when ambiguity materially changes design. "
                "Return strict JSON with keys: "
                "task_summary, candidate_files, assumptions, recommended_steps, risks, validation_approach, open_questions. "
                "open_questions must contain at most 3 questions; each has exactly 4 options with exactly one labeled 'Other'."
            ),
        }
    ]
    payload = {
        "instruction": instruction,
        "repo_summary": repo_summary,
        "evidence": evidence,
        "resolved_answers": answers or [],
    }
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Plan this task using repository evidence and return JSON only:\n" + json.dumps(payload, indent=2),
                }
            ],
        }
    ]
    return system, messages
