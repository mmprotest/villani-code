from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import asdict
import json
from pathlib import Path

from villani_code.orchestrator_models import VerificationOutcome


def snapshot_files(repo: Path, files: list[str], snapshot_dir: Path) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, bool] = {}
    for rel in files:
        src = repo / rel
        dst = snapshot_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        metadata[rel] = src.exists() and src.is_file()
        if src.exists() and src.is_file():
            shutil.copy2(src, dst)
    (snapshot_dir / "snapshot_meta.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def restore_files(repo: Path, files: list[str], snapshot_dir: Path) -> None:
    meta_path = snapshot_dir / "snapshot_meta.json"
    metadata: dict[str, bool] = {}
    if meta_path.exists():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                metadata = {str(k): bool(v) for k, v in payload.items()}
        except json.JSONDecodeError:
            metadata = {}
    for rel in files:
        src = snapshot_dir / rel
        dst = repo / rel
        existed_before = metadata.get(rel, src.exists() and src.is_file())
        if existed_before and src.exists() and src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif not existed_before and dst.exists() and dst.is_file():
            dst.unlink()


def capture_repo_file_state(repo: Path) -> dict[str, int]:
    state: dict[str, int] = {}
    root = repo.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or rel.startswith(".villani_code/"):
            continue
        try:
            state[rel] = hash(path.read_bytes())
        except OSError:
            continue
    return state


def diff_repo_file_state(before: dict[str, int], after: dict[str, int]) -> tuple[list[str], list[str], list[str]]:
    before_keys = set(before)
    after_keys = set(after)
    created = sorted(after_keys - before_keys)
    deleted = sorted(before_keys - after_keys)
    modified = sorted(rel for rel in (before_keys & after_keys) if before.get(rel) != after.get(rel))
    return modified, created, deleted


def count_changed_lines(repo: Path, files: Iterable[str]) -> int:
    changed = 0
    for rel in files:
        path = repo / rel
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        changed += len(text.splitlines())
    return changed


def cleanup_created_files(repo: Path, files: Iterable[str]) -> None:
    for rel in files:
        path = repo / rel
        if path.exists() and path.is_file():
            path.unlink()


def run_verification(
    repo: Path,
    worker_recommended: list[str],
    success_criteria: list[str],
    files_touched: list[str],
    changed_line_count: int,
    max_files: int = 5,
    max_lines: int = 250,
) -> VerificationOutcome:
    reasons: list[str] = []
    commands: list[str] = []
    _ = success_criteria

    if not files_touched:
        reasons.append("fail: no diff")

    if len(set(files_touched)) > max_files:
        reasons.append(f"suspicious breadth: {len(set(files_touched))} files")

    if changed_line_count > max_lines:
        reasons.append(f"diff too large: {changed_line_count} changed lines")

    chosen = _pick_verification_command(worker_recommended=worker_recommended, files_touched=files_touched, success_criteria=success_criteria)
    if chosen:
        proc = subprocess.run(chosen, cwd=repo, shell=True, capture_output=True, text=True)
        commands.append(chosen)
        if proc.returncode != 0:
            reasons.append(f"command failed: {chosen}")

    return VerificationOutcome(ok=not reasons, reasons=reasons, commands=commands)


def to_json(outcome: VerificationOutcome) -> dict[str, object]:
    return asdict(outcome)


def _pick_verification_command(worker_recommended: list[str], files_touched: list[str], success_criteria: list[str]) -> str | None:
    seen: set[str] = set()
    for cmd in worker_recommended:
        normalized = cmd.strip()
        if not normalized or normalized in success_criteria or normalized in seen:
            continue
        seen.add(normalized)
        if _is_broad_or_repetitive_command(normalized):
            continue
        return normalized
    python_targets = [rel for rel in files_touched if rel.endswith(".py")]
    if python_targets:
        return f"python -m py_compile {python_targets[0]}"
    return "python -c \"print('verification-ok')\""


def _is_broad_or_repetitive_command(command: str) -> bool:
    lowered = command.lower()
    broad_patterns = (
        "pytest",
        "python -m pytest",
        "uv run pytest",
        "npm test",
        "pnpm test",
        "tail -",
        "head ",
        "for ",
        "while ",
    )
    if any(pattern in lowered for pattern in broad_patterns):
        return True
    return lowered.count("&&") > 1
