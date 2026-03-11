from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from villani_code.benchmark.policy import enforce_path_policy
from villani_code.patch_apply import apply_unified_diff
from villani_code.prompting import build_initial_messages, build_system_blocks
from villani_code.synthesize.diff_guard import guard_candidate_diff
from villani_code.synthesize.edit_budget import EditBudget
from villani_code.tools import tool_specs


@dataclass(slots=True)
class CandidateExecutionResult:
    changed_files: list[str] = field(default_factory=list)
    changed_line_count: int = 0
    verification_outputs: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    hard_fail: bool = False
    score: float = 0.0
    failure_signature: str = ""
    rejection_reason: str = ""
    patch_artifact_path: str = ""


class CandidateExecutor:
    def __init__(self, runner: Any, instruction: str, max_patch_lines: int, max_files_per_patch: int) -> None:
        self.runner = runner
        self.instruction = instruction
        self.edit_budget = EditBudget(max_files=max_files_per_patch, max_lines=max_patch_lines)

    def evaluate(self, *, suspect: str, hypothesis_id: str, hypothesis_text: str, attempt_id: str, failed_attempt_summary: list[str] | None = None) -> CandidateExecutionResult:
        failed_attempt_summary = failed_attempt_summary or []
        with tempfile.TemporaryDirectory(prefix="villani-weak-search-") as td:
            workspace = Path(td) / "repo"
            shutil.copytree(self.runner.repo, workspace, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
            before_map = self._read_repo_text_map(workspace)
            prompt = self._build_prompt(suspect=suspect, hypothesis_text=hypothesis_text, failed_attempt_summary=failed_attempt_summary)
            self._run_model_edit_pass(workspace, prompt)
            after_map = self._read_repo_text_map(workspace)
            changed_files = sorted([p for p in set(before_map) | set(after_map) if before_map.get(p) != after_map.get(p)])
            changed_lines = sum(self._count_changed_lines(before_map.get(p, ""), after_map.get(p, "")) for p in changed_files)
            if not changed_files:
                return CandidateExecutionResult(hard_fail=True, rejection_reason="no_op_patch", failure_signature="no-op")

            policy = enforce_path_policy(
                changed_files,
                self.runner.benchmark_config.allowlist_paths,
                self.runner.benchmark_config.forbidden_paths,
                expected_paths=self.runner.benchmark_config.expected_files,
                allowed_support_files=self.runner.benchmark_config.allowed_support_files,
                allowed_support_globs=self.runner.benchmark_config.allowed_support_globs,
            )
            guard = guard_candidate_diff(
                files_touched=changed_files,
                changed_lines=changed_lines,
                hunks=max(1, len(changed_files)),
                budget=self.edit_budget,
                benchmark_config=self.runner.benchmark_config,
                formatting_only=False,
            )
            if not guard.allowed or policy.violating_paths:
                reason = guard.reason if not guard.allowed else "path_policy_violation"
                sig = self._fingerprint(reason + "|" + ",".join(policy.violating_paths))
                return CandidateExecutionResult(changed_files=changed_files, changed_line_count=changed_lines, hard_fail=True, rejection_reason=reason, failure_signature=sig)

            verification_outputs, success, score = self._run_verification(workspace, changed_files)
            diff_text = self._build_unified_diff(before_map, after_map, changed_files)
            artifact = self._persist_patch_artifact(attempt_id, diff_text)
            if diff_text.strip():
                apply_unified_diff(self.runner.repo, diff_text)
            sig = self._fingerprint(json.dumps(verification_outputs, sort_keys=True))
            return CandidateExecutionResult(
                changed_files=changed_files,
                changed_line_count=changed_lines,
                verification_outputs=verification_outputs,
                success=success,
                score=score,
                failure_signature=sig,
                patch_artifact_path=artifact,
            )

    def _build_prompt(self, *, suspect: str, hypothesis_text: str, failed_attempt_summary: list[str]) -> str:
        config = self.runner.benchmark_config
        constraints = {
            "allowlist_paths": config.allowlist_paths,
            "forbidden_paths": config.forbidden_paths,
            "expected_files": config.expected_files,
            "max_files_touched": config.max_files_touched,
        }
        return (
            f"Objective: {self.instruction}\n"
            f"Suspect region: {suspect}\n"
            f"Hypothesis: {hypothesis_text}\n"
            f"Constraints: {json.dumps(constraints)}\n"
            f"Failed attempts: {failed_attempt_summary[-3:]}\n"
            "Produce and apply minimal concrete code edits now, then stop."
        )

    def _run_model_edit_pass(self, workspace: Path, prompt: str) -> None:
        original_repo = self.runner.repo
        try:
            self.runner.repo = workspace
            self.runner._ensure_project_memory_and_plan(prompt)
            messages = build_initial_messages(workspace, prompt)
            raw = self.runner.client.create_message({
                "model": self.runner.model,
                "messages": messages,
                "system": build_system_blocks(workspace, benchmark_config=self.runner.benchmark_config),
                "tools": tool_specs(),
                "max_tokens": self.runner.max_tokens,
                "stream": False,
            }, stream=False)
            response = raw if isinstance(raw, dict) else {"content": []}
            for block in [b for b in response.get("content", []) if isinstance(b, dict) and b.get("type") == "tool_use"]:
                self.runner._execute_tool_with_policy(str(block.get("name", "")), dict(block.get("input", {})), str(block.get("id", "cand-tool")), len(messages))
        finally:
            self.runner.repo = original_repo

    def _run_verification(self, workspace: Path, changed_files: list[str]) -> tuple[dict[str, Any], bool, float]:
        outputs: dict[str, Any] = {"commands": []}
        passed = True
        commands = list(self.runner.benchmark_config.visible_verification)
        if not commands:
            commands = ["python -m pytest -q"]
        for cmd in commands:
            proc = subprocess.run(cmd, shell=True, cwd=workspace, text=True, capture_output=True)
            outputs["commands"].append({"command": cmd, "exit_code": proc.returncode, "stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]})
            if proc.returncode != 0:
                passed = False
        score = 1.0 if passed else max(0.0, 1.0 - (sum(1 for c in outputs["commands"] if c["exit_code"] != 0) / max(1, len(outputs["commands"]))))
        if self.runner.benchmark_config.task_id and "repro" in self.runner.benchmark_config.task_id:
            outputs["repro_fingerprint"] = self._fingerprint("\n".join(str(c["stderr"]) for c in outputs["commands"]))
        outputs["changed_files"] = changed_files
        return outputs, passed, score

    def _persist_patch_artifact(self, attempt_id: str, diff_text: str) -> str:
        out_dir = self.runner.repo / ".villani_code" / "patches"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{attempt_id}.diff"
        out.write_text(diff_text, encoding="utf-8")
        return str(out.relative_to(self.runner.repo))

    def _build_unified_diff(self, before: dict[str, str], after: dict[str, str], changed_files: list[str]) -> str:
        chunks: list[str] = []
        for rel in changed_files:
            b = before.get(rel, "").splitlines(keepends=True)
            a = after.get(rel, "").splitlines(keepends=True)
            diff = difflib.unified_diff(b, a, fromfile=f"a/{rel}", tofile=f"b/{rel}")
            chunks.extend(list(diff))
        return "".join(chunks)

    def _read_repo_text_map(self, root: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel.startswith(".git/") or rel.startswith(".villani_code/"):
                continue
            try:
                out[rel] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        return out

    def _count_changed_lines(self, before: str, after: str) -> int:
        return sum(1 for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line.startswith("+") or line.startswith("-"))

    def _fingerprint(self, payload: str) -> str:
        return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:12]
