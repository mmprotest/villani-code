from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    tracked_status: dict[str, str]
    untracked: set[str]
    ignored: set[str]


@dataclass(frozen=True, slots=True)
class WriteLedgerEntry:
    path: str
    operation: str
    actor: str
    intentionally_created: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class IntegrityCheckResult:
    removed_paths: list[str]
    final_changed_paths: list[str]
    finalize_success: bool


class WorkspaceIntegrityGuard:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self._snapshot = self.snapshot()
        self._ledger: list[WriteLedgerEntry] = []

    @property
    def ledger(self) -> list[WriteLedgerEntry]:
        return list(self._ledger)

    def snapshot(self) -> WorkspaceSnapshot:
        tracked = self._git_lines(["status", "--short", "--untracked-files=no"])
        untracked = set(self._git_lines(["ls-files", "--others", "--exclude-standard"]))
        ignored = set(self._git_lines(["ls-files", "--others", "-i", "--exclude-standard"]))
        tracked_status: dict[str, str] = {}
        for line in tracked:
            if len(line) < 4:
                continue
            tracked_status[line[3:]] = line[:2]
        return WorkspaceSnapshot(tracked_status=tracked_status, untracked=untracked, ignored=ignored)

    def record_write(self, *, path: str, operation: str, actor: str, intentionally_created: bool, reason: str = "") -> None:
        self._ledger.append(
            WriteLedgerEntry(
                path=path.replace("\\", "/").lstrip("./"),
                operation=operation,
                actor=actor,
                intentionally_created=intentionally_created,
                reason=reason.strip(),
            )
        )

    def check_and_cleanup(self) -> IntegrityCheckResult:
        current = self.snapshot()
        created_now = (current.untracked | current.ignored) - (self._snapshot.untracked | self._snapshot.ignored)
        created_entries = {e.path: e for e in self._ledger if e.operation == "create"}
        removed: list[str] = []
        for path in sorted(created_now):
            entry = created_entries.get(path)
            if entry is not None and entry.intentionally_created:
                continue
            self._remove_path(path)
            removed.append(path)

        final = self.snapshot()
        changed = sorted(set(final.tracked_status) | (final.untracked - self._snapshot.untracked) | (final.ignored - self._snapshot.ignored))
        meaningful = [p for p in changed if p in final.tracked_status or p in created_entries and created_entries[p].intentionally_created]
        return IntegrityCheckResult(removed_paths=removed, final_changed_paths=changed, finalize_success=bool(meaningful))

    def _remove_path(self, rel: str) -> None:
        target = self.repo_path / rel
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)

    def _git_lines(self, args: list[str]) -> list[str]:
        proc = subprocess.run(["git", *args], cwd=self.repo_path, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
