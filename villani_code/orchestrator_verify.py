from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from villani_code.orchestrator_models import VerificationOutcome


def snapshot_files(repo: Path, files: list[str], snapshot_dir: Path) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for rel in files:
        src = repo / rel
        dst = snapshot_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists() and src.is_file():
            shutil.copy2(src, dst)


def restore_files(repo: Path, files: list[str], snapshot_dir: Path) -> None:
    for rel in files:
        src = snapshot_dir / rel
        dst = repo / rel
        if src.exists() and src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


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

    for cmd in worker_recommended:
        normalized = cmd.strip()
        if not normalized or normalized in success_criteria:
            continue
        proc = subprocess.run(normalized, cwd=repo, shell=True, capture_output=True, text=True)
        commands.append(normalized)
        if proc.returncode != 0:
            reasons.append(f"command failed: {normalized}")
            break

    return VerificationOutcome(ok=not reasons, reasons=reasons, commands=commands)


def to_json(outcome: VerificationOutcome) -> dict[str, object]:
    return asdict(outcome)
