from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)")
_CLAIM_RE = re.compile(r"\b(created|updated|modified|wrote|added|patched|implemented)\b", flags=re.IGNORECASE)


@dataclass(frozen=True)
class CompletionAuditResult:
    passed: bool
    issues: list[str]
    repair_brief: str


def _extract_assistant_text(content_blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text", "")).strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _extract_referenced_paths(text: str) -> list[str]:
    refs: list[str] = []
    for match in _PATH_RE.finditer(text):
        raw = match.group(1).strip().strip("`\"'.,:;()[]{}")
        normalized = raw.replace("\\", "/").lstrip("./")
        if "/" not in normalized:
            continue
        refs.append(normalized)
    seen: set[str] = set()
    ordered: list[str] = []
    for ref in refs:
        if ref and ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ordered


def _file_mentioned_as_mutated(text: str, path: str) -> bool:
    lowered = text.lower()
    path_mentions = {path.lower(), Path(path).name.lower()}
    if not any(token in lowered for token in path_mentions):
        return False
    return bool(_CLAIM_RE.search(text))


def run_completion_audit(
    *,
    repo: Path,
    instruction: str,
    response_content: list[dict[str, Any]],
    transcript: dict[str, Any],
) -> CompletionAuditResult:
    issues: list[str] = []
    assistant_text = _extract_assistant_text(response_content)
    refs = _extract_referenced_paths(assistant_text)

    for rel in refs[:20]:
        candidate = (repo / rel).resolve()
        if not str(candidate).startswith(str(repo.resolve())):
            continue
        if not candidate.exists():
            issues.append(f"Claim references missing path: {rel}")
            continue
        if candidate.is_file() and _file_mentioned_as_mutated(assistant_text, rel) and candidate.stat().st_size == 0:
            issues.append(f"Claimed modified file is empty: {rel}")

    recent_results = transcript.get("tool_results", [])[-8:]
    if recent_results and bool(recent_results[-1].get("is_error")):
        issues.append("Recent tool failures remain unresolved near completion")

    if instruction.strip() and "file" in instruction.lower() and not transcript.get("tool_results"):
        issues.append("No observable tool evidence despite file-oriented request")

    if not issues:
        return CompletionAuditResult(passed=True, issues=[], repair_brief="")

    brief_items = "; ".join(issues[:3])
    repair_brief = (
        "Completion blocked by audit. Fix observed inconsistencies and retry completion: "
        f"{brief_items}."
    )
    return CompletionAuditResult(passed=False, issues=issues, repair_brief=repair_brief)
