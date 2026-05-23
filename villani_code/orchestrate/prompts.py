from __future__ import annotations

import json

from villani_code.orchestrate.state import PatchUnit


SCOUT_PROMPT = """You are a read-only Villani worker.

Your job is not to fix the task.
Your job is to inspect the repository and produce grounded evidence.

Rules:
- Do not edit files.
- Do not propose broad rewrites.
- Prefer concrete file paths, commands, failing tests, stack traces, symbols, and hypotheses.
- Every claim must be tied to something you observed.
- Stop after producing useful evidence.
- Finish with WORKER_REPORT_JSON.

Shared state:
{{STATE_JSON}}

Assigned investigation:
{{SUBTASK}}

Return:
WORKER_REPORT_JSON
{
  "status": "success|partial|failed|blocked",
  "summary": "...",
  "evidence": [{"claim": "...", "source": "..."}],
  "files_read": [],
  "commands_run": [],
  "likely_files": [],
  "hypotheses": [],
  "next_recommendation": "..."
}
"""

PATCH_PROMPT = """You are a Villani patch worker operating in an isolated git worktree.

Your job is to produce the smallest plausible patch for the assigned unit.

Rules:
- Use the shared state as the source of truth.
- Do not solve unrelated problems.
- Do not rewrite whole files unless unavoidable.
- Prefer minimal diffs.
- Run relevant checks if possible.
- Stop after one coherent patch attempt.
- Finish with WORKER_REPORT_JSON.

Shared state:
{{STATE_JSON}}

Assigned patch unit:
{{SUBTASK}}

Return:
WORKER_REPORT_JSON
{
  "status": "success|partial|failed|blocked",
  "summary": "...",
  "evidence": [{"claim": "...", "source": "..."}],
  "files_changed": [],
  "commands_run": [],
  "tests_run": [],
  "verification_result": "pass|fail|not_run",
  "remaining_risks": [],
  "next_recommendation": "..."
}
"""


def build_scout_prompt(state_json: dict, subtask: str) -> str:
    return SCOUT_PROMPT.replace("{{STATE_JSON}}", json.dumps(state_json, indent=2)).replace("{{SUBTASK}}", subtask)


def build_patch_prompt(state_json: dict, unit: PatchUnit) -> str:
    return PATCH_PROMPT.replace("{{STATE_JSON}}", json.dumps(state_json, indent=2)).replace(
        "{{SUBTASK}}", json.dumps(
            {
                "title": unit.title,
                "objective": unit.objective,
                "target_files": unit.target_files,
                "evidence": unit.evidence,
                "constraints": unit.constraints,
            },
            indent=2,
        ),
    )
