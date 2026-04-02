from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from villani_code.patch_apply import PatchApplyError, apply_unified_diff_with_diagnostics, parse_unified_diff


_DIFF_HEADER_RE = re.compile(r"(?m)^---\s+.+\n\+\+\+\s+.+")


@dataclass(frozen=True)
class StdoutPostprocessResult:
    diff_text: str | None
    diagnostics: dict[str, Any]


def extract_unified_diff_from_stdout(stdout: str) -> StdoutPostprocessResult:
    diagnostics: dict[str, Any] = {"stdout_len": len(stdout), "json_detected": False, "candidate_fields": []}
    candidate = _extract_from_json(stdout, diagnostics)
    if candidate is None:
        candidate = _extract_diff_block(stdout)
    diagnostics["diff_found"] = bool(candidate)
    return StdoutPostprocessResult(diff_text=candidate, diagnostics=diagnostics)


def apply_stdout_diff_if_needed(repo_path: Path, stdout: str) -> tuple[list[str], dict[str, Any]]:
    extracted = extract_unified_diff_from_stdout(stdout)
    diagnostics = dict(extracted.diagnostics)
    if not extracted.diff_text:
        diagnostics["applied"] = False
        diagnostics["reason"] = "no_diff"
        return [], diagnostics

    try:
        patches = parse_unified_diff(extracted.diff_text)
    except PatchApplyError as exc:
        diagnostics["applied"] = False
        diagnostics["reason"] = "parse_failed"
        diagnostics["error"] = str(exc)
        return [], diagnostics
    diagnostics["target_files"] = [patch.new_path for patch in patches]
    try:
        touched, apply_diag = apply_unified_diff_with_diagnostics(repo_path, extracted.diff_text)
    except PatchApplyError as exc:
        diagnostics["applied"] = False
        diagnostics["reason"] = "apply_failed"
        diagnostics["error"] = str(exc)
        return [], diagnostics

    diagnostics["applied"] = True
    diagnostics["fallback_files"] = apply_diag.fallback_files
    return touched, diagnostics


def _extract_from_json(stdout: str, diagnostics: dict[str, Any]) -> str | None:
    stripped = stdout.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    diagnostics["json_detected"] = True

    for field, value in _iter_fields(payload):
        if isinstance(value, str) and _extract_diff_block(value):
            diagnostics["candidate_fields"].append(field)
            return _extract_diff_block(value)
    return None


def _iter_fields(payload: Any, prefix: str = "") -> list[tuple[str, Any]]:
    results: list[tuple[str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            field_name = f"{prefix}.{key}" if prefix else str(key)
            results.append((field_name, value))
            results.extend(_iter_fields(value, field_name))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            field_name = f"{prefix}[{index}]"
            results.append((field_name, value))
            results.extend(_iter_fields(value, field_name))
    return results


def _extract_diff_block(text: str) -> str | None:
    if not _DIFF_HEADER_RE.search(text):
        return None
    start = text.find("--- ")
    return text[start:].strip() if start >= 0 else None
