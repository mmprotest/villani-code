from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
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
    diff_stats: dict[str, Any] = field(default_factory=dict)
    patch_artifact_path: str = ""
    verification_outputs: dict[str, Any] = field(default_factory=dict)
    hard_fail: bool = False
    score: float = 0.0
    failure_signature: str = ""
    blocked_reason: str = ""
    attempt_summary: str = ""
    attempt_category: str = "verification_failed"
    success: bool = False
    prompt_summary: str = ""


class CandidateExecutor:
    def __init__(self, runner: Any, instruction: str, max_patch_lines: int, max_files_per_patch: int) -> None:
        self.runner = runner
        self.instruction = instruction
        self.edit_budget = EditBudget(max_files=max_files_per_patch, max_lines=max_patch_lines)

    def evaluate_candidate(
        self,
        *,
        repo_path: Path,
        objective: str,
        suspect_region: str,
        hypothesis_id: str,
        hypothesis: str,
        constraints: dict[str, Any],
        runtime_profile: str,
        benchmark_config: Any,
        baseline_handle: str,
        edit_budget: EditBudget,
        branch_failure_history: list[str],
        timeout_budget_seconds: float,
        attempt_id: str,
    ) -> CandidateExecutionResult:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="villani-weak-search-") as td:
            workspace = Path(td) / "repo"
            shutil.copytree(repo_path, workspace, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
            before_map = self._read_repo_text_map(workspace)
            prompt = self._build_prompt(
                suspect=suspect_region,
                hypothesis_text=hypothesis,
                constraints=constraints,
                failed_attempt_summary=branch_failure_history,
                runtime_profile=runtime_profile,
                baseline_handle=baseline_handle,
            )
            model_err = self._run_model_edit_pass(workspace, prompt)
            if model_err:
                return CandidateExecutionResult(
                    hard_fail=True,
                    blocked_reason="blocked_model_failure",
                    attempt_category="blocked_model_failure",
                    failure_signature=self._fingerprint(model_err),
                    attempt_summary=model_err,
                    prompt_summary=prompt[:220],
                )

            after_map = self._read_repo_text_map(workspace)
            changed_files = sorted([p for p in set(before_map) | set(after_map) if before_map.get(p) != after_map.get(p)])
            changed_lines = sum(self._count_changed_lines(before_map.get(p, ""), after_map.get(p, "")) for p in changed_files)
            diff_stats = {"changed_file_count": len(changed_files), "changed_line_count": changed_lines}

            if not changed_files:
                return CandidateExecutionResult(
                    hard_fail=True,
                    blocked_reason="rejected_noop",
                    attempt_category="rejected_noop",
                    failure_signature="no-op",
                    attempt_summary="Model produced no file changes.",
                    prompt_summary=prompt[:220],
                )

            policy = enforce_path_policy(
                changed_files,
                benchmark_config.allowlist_paths,
                benchmark_config.forbidden_paths,
                expected_paths=benchmark_config.expected_files,
                allowed_support_files=benchmark_config.allowed_support_files,
                allowed_support_globs=benchmark_config.allowed_support_globs,
            )
            guard = guard_candidate_diff(
                files_touched=changed_files,
                changed_lines=changed_lines,
                hunks=max(1, len(changed_files)),
                budget=edit_budget,
                benchmark_config=benchmark_config,
                formatting_only=False,
            )
            if not guard.allowed or policy.violating_paths:
                reason = "rejected_diff_guard" if not guard.allowed else "blocked_policy"
                sig = self._fingerprint(reason + "|" + ",".join(policy.violating_paths))
                return CandidateExecutionResult(
                    changed_files=changed_files,
                    diff_stats=diff_stats,
                    hard_fail=True,
                    blocked_reason=reason,
                    attempt_category=reason,
                    failure_signature=sig,
                    attempt_summary=guard.reason if not guard.allowed else f"Violating paths: {policy.violating_paths}",
                    prompt_summary=prompt[:220],
                )

            verification_outputs, success, score = self._run_verification(workspace, changed_files, benchmark_config)
            diff_text = self._build_unified_diff(before_map, after_map, changed_files)
            if not diff_text.strip():
                return CandidateExecutionResult(
                    hard_fail=True,
                    blocked_reason="rejected_noop",
                    attempt_category="rejected_noop",
                    failure_signature="empty-diff",
                    attempt_summary="Changes detected but unified diff empty.",
                    prompt_summary=prompt[:220],
                )
            artifact = self._persist_patch_artifact(attempt_id, diff_text)
            apply_unified_diff(repo_path, diff_text)

            elapsed = time.monotonic() - started
            if elapsed > timeout_budget_seconds:
                return CandidateExecutionResult(
                    changed_files=changed_files,
                    diff_stats=diff_stats,
                    patch_artifact_path=artifact,
                    verification_outputs=verification_outputs,
                    hard_fail=True,
                    blocked_reason="blocked_timeout",
                    attempt_category="blocked_timeout",
                    failure_signature=self._fingerprint("timeout" + attempt_id),
                    attempt_summary="Candidate exceeded timeout budget.",
                    prompt_summary=prompt[:220],
                )

            failure_category = self._major_error_category(verification_outputs)
            failure_sig_payload = {
                "files": changed_files,
                "verification": verification_outputs.get("summary", ""),
                "repro_fingerprint": verification_outputs.get("repro_fingerprint", ""),
                "category": failure_category,
            }
            attempt_category = "applied_and_verified" if success else "verification_failed"
            return CandidateExecutionResult(
                changed_files=changed_files,
                diff_stats=diff_stats,
                patch_artifact_path=artifact,
                verification_outputs=verification_outputs,
                hard_fail=False,
                score=score,
                failure_signature=self._fingerprint(json.dumps(failure_sig_payload, sort_keys=True)),
                blocked_reason="",
                attempt_summary=f"Applied patch touching {len(changed_files)} file(s).",
                attempt_category=attempt_category,
                success=success,
                prompt_summary=prompt[:220],
            )

    def evaluate(self, **kwargs: Any) -> CandidateExecutionResult:
        return self.evaluate_candidate(**kwargs)

    def _build_prompt(self, *, suspect: str, hypothesis_text: str, constraints: dict[str, Any], failed_attempt_summary: list[str], runtime_profile: str, baseline_handle: str) -> str:
        return (
            f"Objective: {self.instruction}\n"
            f"Runtime profile: {runtime_profile}\n"
            f"Baseline: {baseline_handle}\n"
            f"Suspect region: {suspect}\n"
            f"Hypothesis: {hypothesis_text}\n"
            f"Constraints: {json.dumps(constraints)}\n"
            f"Failed attempts: {failed_attempt_summary[-3:]}\n"
            "Apply minimal concrete edits in repository files and stop when done."
        )

    def _run_model_edit_pass(self, workspace: Path, prompt: str) -> str:
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
            blocks = [b for b in response.get("content", []) if isinstance(b, dict) and b.get("type") == "tool_use"]
            if not blocks:
                return "model returned no tool actions"
            for block in blocks:
                self.runner._execute_tool_with_policy(str(block.get("name", "")), dict(block.get("input", {})), str(block.get("id", "cand-tool")), len(messages))
        except Exception as exc:  # noqa: BLE001
            return str(exc)
        finally:
            self.runner.repo = original_repo
        return ""

    def _run_verification(self, workspace: Path, changed_files: list[str], benchmark_config: Any) -> tuple[dict[str, Any], bool, float]:
        outputs: dict[str, Any] = {"commands": []}
        passed = True
        commands = list(benchmark_config.visible_verification)
        if not commands:
            commands = ["python -m pytest -q"]
        for cmd in commands:
            proc = subprocess.run(cmd, shell=True, cwd=workspace, text=True, capture_output=True)
            outputs["commands"].append({"command": cmd, "exit_code": proc.returncode, "stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]})
            if proc.returncode != 0:
                passed = False
        outputs["summary"] = "ok" if passed else "verification_failed"
        if benchmark_config.task_id and "repro" in benchmark_config.task_id:
            outputs["repro_command"] = commands[0]
            outputs["repro_fingerprint"] = self._fingerprint("\n".join(f"{c['exit_code']}|{c['stderr']}" for c in outputs["commands"]))
        outputs["changed_files"] = changed_files
        target_ok = sum(1 for c in outputs["commands"] if c["exit_code"] == 0)
        collateral_ok = 1.0 if passed else 0.5
        minimality = max(0.0, 1.0 - (len(changed_files) / max(1, self.edit_budget.max_files)))
        score = round((0.55 * (target_ok / max(1, len(outputs["commands"]))) + 0.15 * collateral_ok + 0.2 * minimality + 0.1), 4)
        return outputs, passed, score

    def _major_error_category(self, verification_outputs: dict[str, Any]) -> str:
        if verification_outputs.get("summary") == "ok":
            return "none"
        if verification_outputs.get("repro_fingerprint"):
            return "repro_failure"
        return "verification_failure"

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
