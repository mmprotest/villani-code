from __future__ import annotations

DEFAULT_PROMPT_TEMPLATE = """You are working in /testbed.

Resolve the repository issue described below.
Make only the changes needed to fix it.
Run tests or verification commands when useful.
Do not output a patch manually. Edit the repository directly.

Issue:
{problem_statement}

When finished, stop.
"""


def build_default_prompt(problem_statement: str, *, accessible_repo_path: str | None = None) -> str:
    statement = problem_statement.strip()
    if not statement:
        raise ValueError("problem_statement must not be empty")
    prompt = DEFAULT_PROMPT_TEMPLATE.format(problem_statement=statement)
    if accessible_repo_path and accessible_repo_path != "/testbed":
        prompt = (
            prompt.rstrip()
            + "\n\n"
            + f"The benchmark repo corresponds to /testbed, but in your runtime it is mounted at {accessible_repo_path}.\n"
        )
    return prompt
