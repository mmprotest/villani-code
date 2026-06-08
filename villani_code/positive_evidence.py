from __future__ import annotations

import json
import re
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

MAX_POSITIVE_EVIDENCE_WARNINGS = 5
MAX_POSITIVE_EVIDENCE_SNIPPET_CHARS = 180
POSITIVE_EVIDENCE_WARNING_PREFIX = "Unresolved positive evidence remains:"

_SEARCH_TOOLS = {"grep", "search"}
_LISTING_TOOLS = {"ls", "glob"}
_SEARCH_EXECUTABLES = {"grep", "egrep", "fgrep", "rg", "ag"}
_WEAKENING_PATTERNS = (
    re.compile(r"\|\s*head(?:\s|$)"),
    re.compile(r"\|\s*tail(?:\s|$)"),
    re.compile(r"\|\s*grep\s+[^|]*-[^|]*v(?:\s|$)"),
    re.compile(r"(?:^|[;&|]\s*)grep\s+[^|]*-[^|]*v(?:\s|$)"),
    re.compile(r"(?:^|\s)!\s+-path(?:\s|$)"),
    re.compile(r"--exclude(?:-dir)?(?:=|\s)"),
)
_LINE_MATCH_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<snippet>.*)$")
_SNIPPET_MATCH_RE = re.compile(r"^(?P<path>[^:\r\n]+):(?P<snippet>.+)$")


def _cap_snippet(value: str) -> str:
    compact = " ".join(value.strip().split())
    if len(compact) <= MAX_POSITIVE_EVIDENCE_SNIPPET_CHARS:
        return compact
    return compact[: MAX_POSITIVE_EVIDENCE_SNIPPET_CHARS - 1] + "…"


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def command_is_weakened(command: str, *, output_truncated: bool = False) -> tuple[bool, list[str]]:
    reasons = [pattern.pattern for pattern in _WEAKENING_PATTERNS if pattern.search(command)]
    if output_truncated:
        reasons.append("output_truncation")
    return bool(reasons), reasons


def _search_command_details(command: str) -> dict[str, Any] | None:
    tokens = _command_tokens(command)
    if not tokens:
        return None
    executable_index = -1
    executable = ""
    for index, token in enumerate(tokens):
        name = Path(token).name
        if name == "git" and index + 1 < len(tokens) and tokens[index + 1] == "grep":
            executable_index = index
            executable = "git grep"
            break
        if name in _SEARCH_EXECUTABLES:
            executable_index = index
            executable = name
            break
    if executable_index < 0:
        return None
    args = tokens[executable_index + (2 if executable == "git grep" else 1) :]
    file_only = False
    pattern = ""
    scopes: list[str] = []
    skip_value = False
    expect_pattern = False
    pattern_flags = {"-e", "--regexp"}
    value_flags = {"-A", "-B", "-C", "--after-context", "--before-context", "--context", "-g", "--glob", "--type", "--type-add"}
    for token in args:
        if token in {"|", ";", "&&", "||"}:
            break
        if expect_pattern:
            if not pattern:
                pattern = token
            expect_pattern = False
            continue
        if skip_value:
            skip_value = False
            continue
        if token in value_flags:
            skip_value = True
            continue
        if token in pattern_flags:
            expect_pattern = True
            continue
        if token == "--":
            continue
        if token.startswith("-"):
            if "l" in token[1:] or token == "--files-with-matches":
                file_only = True
            continue
        if not pattern:
            pattern = token
        else:
            scopes.append(token)
    return {
        "executable": executable,
        "file_only": file_only,
        "pattern": pattern,
        "scope": scopes[-1] if scopes else ".",
    }


def _scope_contains(candidate_scope: str, original_scope: str) -> bool:
    candidate = candidate_scope.replace("\\", "/").strip() or "."
    original = original_scope.replace("\\", "/").strip() or "."
    candidate = candidate.removeprefix("./").rstrip("/") or "."
    original = original.removeprefix("./").rstrip("/") or "."
    return candidate == "." or candidate == original or original.startswith(candidate + "/")


@dataclass(slots=True)
class PositiveEvidenceEntry:
    path: str
    source_tool: str
    source_command: str
    source_tool_id: str
    matched_snippet: str
    source_output_truncated: bool
    source_command_filtered: bool
    first_seen_turn: int
    last_seen_turn: int
    status: str = "unresolved"
    occurrences: int = 1
    search_pattern: str = ""
    search_scope: str = "."
    status_reason: str = "positive content match has not been inspected, modified, or cleared"
    status_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PositiveEvidenceLedger:
    repo: Path
    entries: list[PositiveEvidenceEntry] = field(default_factory=list)
    warning_events: list[dict[str, Any]] = field(default_factory=list)

    def _normalize_path(self, raw_path: str) -> str:
        value = raw_path.strip().strip('"\'').replace("\\", "/")
        if not value:
            return ""
        path = Path(value)
        if path.is_absolute():
            try:
                return path.resolve(strict=False).relative_to(self.repo.resolve(strict=False)).as_posix()
            except ValueError:
                return path.as_posix()
        normalized = Path(value.removeprefix("./")).as_posix()
        return "" if normalized in {"", "."} else normalized

    def _set_status(self, entry: PositiveEvidenceEntry, status: str, turn: int, reason: str, **details: Any) -> None:
        previous = entry.status
        entry.status = status
        entry.last_seen_turn = max(entry.last_seen_turn, turn)
        entry.status_reason = reason
        entry.status_history.append(
            {"turn": turn, "from": previous, "to": status, "reason": reason, **details}
        )

    def _upsert(
        self,
        *,
        path: str,
        snippet: str,
        source_tool: str,
        source_command: str,
        source_tool_id: str,
        output_truncated: bool,
        filtered: bool,
        turn: int,
        pattern: str,
        scope: str,
    ) -> None:
        normalized = self._normalize_path(path)
        if not normalized:
            return
        snippet = _cap_snippet(snippet)
        existing = next(
            (
                item
                for item in self.entries
                if item.path == normalized
                and item.matched_snippet == snippet
                and item.search_pattern == pattern
            ),
            None,
        )
        if existing is not None:
            existing.occurrences += 1
            existing.last_seen_turn = turn
            if existing.status in {"cleared", "dismissed_with_evidence"}:
                self._set_status(
                    existing,
                    "unresolved",
                    turn,
                    "positive evidence appeared again after it was resolved",
                    source_tool_id=source_tool_id,
                )
            return
        self.entries.append(
            PositiveEvidenceEntry(
                path=normalized,
                source_tool=source_tool,
                source_command=source_command,
                source_tool_id=source_tool_id,
                matched_snippet=snippet,
                source_output_truncated=output_truncated,
                source_command_filtered=filtered,
                first_seen_turn=turn,
                last_seen_turn=turn,
                search_pattern=pattern,
                search_scope=self._normalize_path(scope) or ".",
                status_history=[
                    {
                        "turn": turn,
                        "from": None,
                        "to": "unresolved",
                        "reason": "positive match discovered",
                        "source_tool_id": source_tool_id,
                    }
                ],
            )
        )

    def observe_tool_result(
        self,
        tool_name: str,
        tool_input: Mapping[str, Any],
        content: str,
        *,
        turn: int,
        tool_use_id: str = "",
        is_error: bool = False,
    ) -> dict[str, Any]:
        tool = tool_name.lower()
        if is_error:
            return {"weakened_clear_attempt": False}
        if tool == "read":
            self.mark_inspected(str(tool_input.get("file_path", "")), turn, tool_use_id=tool_use_id)
            return {"weakened_clear_attempt": False}
        if tool in {"write", "patch", "edit"}:
            self.mark_modified(str(tool_input.get("file_path", "")), turn, tool_use_id=tool_use_id)
            return {"weakened_clear_attempt": False}
        if tool in _LISTING_TOOLS:
            return {"weakened_clear_attempt": False}

        source_command = ""
        pattern = ""
        scope = "."
        file_only = False
        search_source = tool in _SEARCH_TOOLS
        if tool == "bash":
            source_command = str(tool_input.get("command", ""))
            details = _search_command_details(source_command)
            if details is None:
                self._mark_command_mutations(tool_input, turn, tool_use_id)
                return {"weakened_clear_attempt": False}
            search_source = True
            pattern = str(details["pattern"])
            scope = str(details["scope"])
            file_only = bool(details["file_only"])
            content = self._bash_stdout(content)
        elif search_source:
            pattern = str(tool_input.get("pattern") or tool_input.get("query") or "")
            scope = str(tool_input.get("path", "."))
            source_command = f"{tool_name} pattern={pattern!r} path={scope!r}"
        else:
            return {"weakened_clear_attempt": False}

        output_truncated = "[truncated" in content.lower()
        weakened, weakening_reasons = command_is_weakened(source_command, output_truncated=output_truncated)
        parsed = self._parse_matches(content, file_only=file_only)
        if parsed:
            for path, snippet in parsed:
                self._upsert(
                    path=path,
                    snippet=snippet,
                    source_tool=tool_name,
                    source_command=source_command,
                    source_tool_id=tool_use_id,
                    output_truncated=output_truncated,
                    filtered=weakened,
                    turn=turn,
                    pattern=pattern,
                    scope=scope,
                )
            return {"weakened_clear_attempt": False, "matches_recorded": len(parsed)}

        unresolved_before = [entry for entry in self.entries if entry.status in {"unresolved", "inspected"}]
        clearable = [
            entry
            for entry in unresolved_before
            if pattern
            and pattern == entry.search_pattern
            and _scope_contains(scope, entry.search_scope)
        ]
        if weakened and clearable:
            for entry in clearable:
                entry.status_history.append(
                    {
                        "turn": turn,
                        "from": entry.status,
                        "to": entry.status,
                        "reason": "validation did not clear evidence because it was weakened",
                        "weakening_reasons": weakening_reasons,
                        "source_tool_id": tool_use_id,
                    }
                )
            return {"weakened_clear_attempt": True, "entries": [entry.path for entry in clearable]}
        if not weakened:
            for entry in clearable:
                self._set_status(
                    entry,
                    "cleared",
                    turn,
                    "equal-or-broader unfiltered validation found no matching evidence",
                    source_tool_id=tool_use_id,
                    validation_scope=scope,
                    validation_pattern=pattern,
                )
        return {"weakened_clear_attempt": False, "entries_cleared": len(clearable) if not weakened else 0}

    @staticmethod
    def _bash_stdout(content: str) -> str:
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return content
        return str(payload.get("stdout", "")) if isinstance(payload, dict) else content

    @staticmethod
    def _parse_matches(content: str, *, file_only: bool) -> list[tuple[str, str]]:
        matches: list[tuple[str, str]] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("--", "Warning:", "[truncated")):
                continue
            line_match = _LINE_MATCH_RE.match(line)
            if line_match:
                matches.append((line_match.group("path"), line_match.group("snippet")))
                continue
            snippet_match = _SNIPPET_MATCH_RE.match(line)
            if snippet_match and not re.match(r"^[A-Za-z]:[\\/]", line):
                path = snippet_match.group("path")
                if "/" in path or "." in Path(path).name:
                    matches.append((path, snippet_match.group("snippet")))
                    continue
            if file_only and not any(char.isspace() for char in line):
                matches.append((line, ""))
        return matches

    def _mark_command_mutations(self, tool_input: Mapping[str, Any], turn: int, tool_use_id: str) -> None:
        command = str(tool_input.get("command", ""))
        for entry in self.entries:
            if entry.status in {"unresolved", "inspected"} and entry.path in command:
                if re.search(r"(?:^|[;&|]\s*)(?:sed\s+-i|perl\s+-pi|truncate|tee|cp|mv)\b", command):
                    self._set_status(entry, "modified", turn, "command modified the matched file", source_tool_id=tool_use_id)

    def mark_inspected(self, path: str, turn: int, *, tool_use_id: str = "") -> None:
        normalized = self._normalize_path(path)
        for entry in self.entries:
            if entry.path == normalized and entry.status == "unresolved":
                self._set_status(
                    entry,
                    "inspected",
                    turn,
                    "matched file was inspected; explicit evidence is still required to dismiss it",
                    source_tool_id=tool_use_id,
                )

    def mark_modified(self, path: str, turn: int, *, tool_use_id: str = "") -> None:
        normalized = self._normalize_path(path)
        for entry in self.entries:
            if entry.path == normalized and entry.status in {"unresolved", "inspected"}:
                self._set_status(entry, "modified", turn, "matched file was modified", source_tool_id=tool_use_id)

    def observe_agent_text(self, text: str, turn: int) -> int:
        normalized_text = " ".join(text.split())
        lowered = normalized_text.lower()
        evidence_markers = ("because", "since", "confirmed", "inspection shows", "read shows")
        dismissal_markers = ("benign", "expected", "false positive", "not relevant", "safe")
        if not any(marker in lowered for marker in evidence_markers):
            return 0
        dismissed = 0
        for entry in self.entries:
            if entry.status != "inspected" or entry.path not in normalized_text:
                continue
            if any(marker in lowered for marker in dismissal_markers):
                if self.dismiss_with_evidence(entry.path, normalized_text, turn):
                    dismissed += 1
        return dismissed

    def dismiss_with_evidence(self, path: str, evidence: str, turn: int) -> bool:
        normalized = self._normalize_path(path)
        if not evidence.strip():
            return False
        changed = False
        for entry in self.entries:
            if entry.path == normalized and entry.status == "inspected":
                self._set_status(
                    entry,
                    "dismissed_with_evidence",
                    turn,
                    "agent dismissed inspected evidence with specific support",
                    dismissal_evidence=_cap_snippet(evidence),
                )
                changed = True
        return changed

    def _rank(self, entry: PositiveEvidenceEntry, current_turn: int) -> tuple[int, int, int, int]:
        score = 0
        score += 40 if entry.matched_snippet else 10
        score += min(entry.occurrences, 5) * 8
        score += 8 if entry.status == "unresolved" else 2
        score += 6 if not entry.source_output_truncated else -6
        score += 4 if not entry.source_command_filtered else -4
        score += max(0, 8 - max(0, current_turn - entry.last_seen_turn))
        return score, entry.occurrences, entry.last_seen_turn, -entry.first_seen_turn

    def unresolved(self) -> list[PositiveEvidenceEntry]:
        return [entry for entry in self.entries if entry.status in {"unresolved", "inspected"}]

    def render_warning(self, *, current_turn: int, reason: str, record: bool = True) -> str:
        ranked = sorted(self.unresolved(), key=lambda entry: self._rank(entry, current_turn), reverse=True)
        if not ranked:
            return ""
        lines = [POSITIVE_EVIDENCE_WARNING_PREFIX]
        shown = ranked[:MAX_POSITIVE_EVIDENCE_WARNINGS]
        for entry in shown:
            evidence = f' — "{entry.matched_snippet}"' if entry.matched_snippet else ""
            unresolved_reason = (
                "inspected but not dismissed with evidence"
                if entry.status == "inspected"
                else "not yet inspected, modified, or cleared"
            )
            lines.append(
                f"- {entry.path}{evidence}; {unresolved_reason}. Next: inspect or modify it, "
                "or clear it with equal-or-broader unfiltered validation."
            )
        warning = "\n".join(lines)
        if record:
            self.warning_events.append(
                {
                    "turn": current_turn,
                    "reason": reason,
                    "warning": warning,
                    "entry_paths": [entry.path for entry in shown],
                }
            )
        return warning

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [entry.to_dict() for entry in self.entries]}
