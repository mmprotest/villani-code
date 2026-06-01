from __future__ import annotations

import json
import re
import shlex
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ActionClassification = Literal[
    "mutation",
    "validation",
    "reconnaissance",
    "administrative",
    "unknown",
]
ValidationResult = Literal["none", "useful_failure", "useful_success", "inconclusive"]

_READONLY_PREFIXES = (
    "pwd",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "rg",
    "grep",
    "find",
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git rev-parse",
    "git ls-files",
)
_ADMIN_PREFIXES = (
    "git status",
    "git log",
    "git branch",
    "git rev-parse",
    "which",
    "whoami",
    "date",
    "env",
    "printenv",
)
_MUTATION_PATTERNS = (
    r"(^|\s)(pip|python\s+-m\s+pip|uv|poetry|npm|pnpm|yarn|cargo|go)\s+[^;&|]*(install|add|remove|update|sync|mod\s+tidy)\b",
    r"(^|\s)(touch|mkdir|rm|rmdir|mv|cp|chmod|chown|ln)\b",
    r"(^|\s)git\s+(add|commit|push|pull|merge|rebase|checkout|switch|restore|reset|clean|tag|cherry-pick|apply|am)\b",
    r"(^|\s)(sed\s+-i|perl\s+-pi)\b",
    r"(^|\s)(docker|podman|docker-compose)\s+[^;&|]*(run|build|compose|up|start|restart|stop|rm)\b",
    r"(^|\s)(make|ninja|cmake)\s+[^;&|]*(install|clean)\b",
    r"(^|\s)(python|node|bash|sh)\s+[^;&|]*(generate|seed|migrate|write|export)\b",
    r"(>|>>|\|\s*tee\b)",
)
_VALIDATION_PATTERNS = (
    r"(^|\s)(pytest|python\s+-m\s+pytest|tox|nox|unittest|go\s+test|cargo\s+test|npm\s+test|pnpm\s+test|yarn\s+test|vitest|jest|mocha|ctest|bats)\b",
    r"(^|\s)(ruff|mypy|pyright|eslint|tsc|cargo\s+check|cargo\s+clippy|go\s+vet|flake8|pylint|shellcheck)\b",
    r"(^|\s)(make|ninja|cmake|cargo|go|npm|pnpm|yarn)\s+[^;&|]*(test|check|build|compile|lint|verify|validate)\b",
    r"(^|\s)(python|node|ruby|perl|php|java|bash|sh)\b.*\b(import|require|assert|check|test|verify|validate)\b",
    r"(^|\s)(curl|wget|http|grpcurl|nc)\b",
    r"(^|\s)(diff|cmp)\b",
)
_SHELL_SYNTAX_FAILURES = (
    "syntax error",
    "command not found",
    "no such file or directory",
    "permission denied",
    "timed out",
)
_ADMIN_FAILURE_MARKERS = (
    "could not install",
    "failed to install",
    "dependency resolution",
    "permission denied",
    "network is unreachable",
    "temporary failure in name resolution",
)


def _norm_command(command: str) -> str:
    return " ".join(str(command or "").strip().lower().split())


def _matches_any(command: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, command) for pattern in patterns)


def classify_action(tool_name: str, tool_input: dict[str, Any] | None = None, result: dict[str, Any] | None = None) -> ActionClassification:
    """Classify a tool action by general intent, without task-specific rules."""

    tool_input = tool_input or {}
    result = result or {}
    if tool_name in {"Write", "Patch", "Edit", "GitCheckout", "GitCommit"}:
        return "mutation"
    if tool_name in {"Read", "Ls", "Grep", "Glob", "Search", "WebFetch"}:
        return "reconnaissance"
    if tool_name == "SubmitPlan":
        return "administrative"
    if tool_name != "Bash":
        return "unknown"

    command = _norm_command(str(tool_input.get("command", "")))
    if not command:
        return "unknown"
    if _matches_any(command, _MUTATION_PATTERNS):
        return "mutation"
    if _matches_any(command, _VALIDATION_PATTERNS):
        return "validation"
    if command.startswith(_ADMIN_PREFIXES):
        return "administrative"
    if command.startswith(_READONLY_PREFIXES):
        return "reconnaissance"
    # Program execution with no obvious mutation is potentially behavioural feedback.
    try:
        first = shlex.split(command)[0] if shlex.split(command) else ""
    except ValueError:
        first = command.split(" ", 1)[0]
    if first in {"python", "python3", "node", "ruby", "perl", "php", "java", "bash", "sh", "./run", "./app"}:
        return "validation"
    return "unknown"


def bash_result_details(result: dict[str, Any]) -> tuple[int | None, str, str]:
    content = str(result.get("content", ""))
    try:
        decoded = json.loads(content)
    except Exception:
        decoded = {}
    if not isinstance(decoded, dict):
        decoded = {}
    exit_code = decoded.get("exit_code")
    stdout = str(decoded.get("stdout", ""))
    stderr = str(decoded.get("stderr", ""))
    return (exit_code if isinstance(exit_code, int) else None, stdout, stderr)


def summarize_output(text: str, limit: int = 700) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return "No output was produced."
    selected = "\n".join(lines[-12:])
    if len(selected) > limit:
        selected = selected[-limit:]
    return selected


def validation_result(tool_name: str, tool_input: dict[str, Any], result: dict[str, Any], classification: ActionClassification | None = None) -> ValidationResult:
    classification = classification or classify_action(tool_name, tool_input, result)
    if classification != "validation":
        return "none"
    if tool_name == "Bash":
        command = _norm_command(str(tool_input.get("command", "")))
        exit_code, stdout, stderr = bash_result_details(result)
        output = f"{stdout}\n{stderr}".lower()
        if exit_code == 0:
            return "useful_success"
        if exit_code is None:
            return "inconclusive"
        if _matches_any(command, _MUTATION_PATTERNS):
            return "inconclusive"
        if any(marker in output for marker in _ADMIN_FAILURE_MARKERS):
            return "inconclusive"
        # Shell syntax and missing-command failures are useful only for direct runnable invocations,
        # not for accidental malformed validation commands.
        if any(marker in output for marker in _SHELL_SYNTAX_FAILURES) and not _matches_any(command, _VALIDATION_PATTERNS):
            return "inconclusive"
        return "useful_failure"
    return "useful_failure" if result.get("is_error") else "useful_success"


@dataclass
class VerificationDebtState:
    verification_debt: int = 0
    actions_since_validation: int = 0
    mutations_since_validation: int = 0
    last_validation_action: str | None = None
    last_validation_output: str | None = None
    last_validation_result: ValidationResult = "none"
    last_validation_summary: str | None = None
    validation_intervention_count: int = 0
    recent_mutations: list[str] = field(default_factory=list)
    recent_errors: list[str] = field(default_factory=list)
    telemetry: list[dict[str, Any]] = field(default_factory=list)
    threshold: int = 5
    min_mutations_before_intervention: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def record_action(self, tool_name: str, tool_input: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        classification = classify_action(tool_name, tool_input, result)
        action = self._describe_action(tool_name, tool_input)
        validation = validation_result(tool_name, tool_input, result, classification)
        content_lower = str(result.get("content", "")).lower()
        if classification == "mutation" and (
            result.get("is_error")
            or "not executed" in content_lower
            or "blocked" in content_lower
            or "denied" in content_lower
        ):
            classification = "unknown"
            validation = "none"
        event: dict[str, Any] = {
            "action": action,
            "tool_name": tool_name,
            "classification": classification,
            "validation_result": validation,
            "debt_before": self.verification_debt,
        }
        if classification == "mutation":
            self.actions_since_validation += 1
            self.mutations_since_validation += 1
            increment = 1 + max(0, self.mutations_since_validation - 1)
            if self.recent_mutations and action in self.recent_mutations[-3:]:
                increment += 1
            self.verification_debt += increment
            self.recent_mutations.append(action)
            self.recent_mutations = self.recent_mutations[-8:]
        elif classification == "validation":
            self.actions_since_validation += 1
            self.last_validation_action = action
            output = self._extract_output(tool_name, result)
            self.last_validation_output = output[-2000:] if output else ""
            self.last_validation_result = validation
            self.last_validation_summary = summarize_output(output)
            if validation == "useful_success":
                self.verification_debt = 0
                self.actions_since_validation = 0
                self.mutations_since_validation = 0
                self.recent_errors.clear()
            elif validation == "useful_failure":
                self.verification_debt = min(self.verification_debt, 1)
                self.actions_since_validation = 0
                self.mutations_since_validation = 0
                if self.last_validation_summary:
                    self.recent_errors.append(self.last_validation_summary)
                    self.recent_errors = self.recent_errors[-3:]
            else:
                self.verification_debt = max(0, self.verification_debt - 1)
        elif classification in {"reconnaissance", "administrative"}:
            self.actions_since_validation += 1
        else:
            self.actions_since_validation += 1
            if result.get("is_error"):
                self.recent_errors.append(summarize_output(str(result.get("content", ""))))
                self.recent_errors = self.recent_errors[-3:]
        event["debt_after"] = self.verification_debt
        event["actions_since_validation"] = self.actions_since_validation
        event["mutations_since_validation"] = self.mutations_since_validation
        self.telemetry.append(event)
        return event

    def should_intervene(self) -> bool:
        return (
            self.verification_debt >= self.threshold
            and self.mutations_since_validation >= self.min_mutations_before_intervention
        )

    def build_validation_intervention(self) -> str:
        self.validation_intervention_count += 1
        parts = [
            "You have made several changes without obtaining fresh evidence that the current approach works.",
            "",
            "Pause further speculative editing. Choose and run the most informative validation action currently available. This may be a test, build, import, executable invocation, service request, output inspection, comparison, or another direct behavioural check.",
            "",
            "Use the resulting output to decide the next change. If no meaningful validation is possible yet, briefly state why and perform the minimum work needed to make validation possible.",
        ]
        if self.recent_mutations:
            parts.extend(["", "Recent mutations contributing to verification debt:"])
            parts.extend(f"- {item}" for item in self.recent_mutations[-5:])
        if self.last_validation_result != "none":
            parts.extend(["", f"Last validation result: {self.last_validation_result}"])
            if self.last_validation_action:
                parts.append(f"Last validation action: {self.last_validation_action}")
            if self.last_validation_summary:
                parts.append(f"Last validation summary: {self.last_validation_summary}")
        if self.recent_errors:
            parts.extend(["", "Recent unresolved error output:", self.recent_errors[-1]])
        return "\n".join(parts)

    def build_failed_validation_guidance(self) -> str:
        if self.last_validation_result != "useful_failure":
            return ""
        return (
            "The most recent validation action produced actionable failure evidence.\n\n"
            f"Validation action:\n{self.last_validation_action or '(unknown)'}\n\n"
            f"Observed failure:\n{self.last_validation_summary or '(no concise summary available)'}\n\n"
            "Use this evidence to make the smallest targeted change that addresses the failure, then obtain fresh validation feedback again."
        )

    def _describe_action(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "Bash":
            return str(tool_input.get("command", "")).strip()[:300]
        if tool_name in {"Write", "Read"}:
            return f"{tool_name} {tool_input.get('file_path', '')}".strip()
        if tool_name == "Patch":
            path = str(tool_input.get("file_path", "")).strip()
            return f"Patch {path}".strip()
        return tool_name

    def _extract_output(self, tool_name: str, result: dict[str, Any]) -> str:
        if tool_name == "Bash":
            _exit, stdout, stderr = bash_result_details(result)
            return f"{stdout}\n{stderr}".strip()
        return str(result.get("content", ""))
