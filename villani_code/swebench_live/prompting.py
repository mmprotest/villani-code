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


def build_default_prompt(problem_statement: str) -> str:
    statement = problem_statement.strip()
    if not statement:
        raise ValueError("problem_statement must not be empty")
    return DEFAULT_PROMPT_TEMPLATE.format(problem_statement=statement)
