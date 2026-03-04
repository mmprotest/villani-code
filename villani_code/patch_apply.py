from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


class PatchApplyError(Exception):
    pass


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


@dataclass
class FilePatch:
    old_path: str
    new_path: str
    hunks: list[Hunk]


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def apply_unified_diff(repo: Path, diff_text: str, default_file_path: str | None = None) -> list[str]:
    patches = parse_unified_diff(diff_text)
    if not patches:
        if default_file_path:
            raise PatchApplyError(f"No file patch found in unified diff for {default_file_path}")
        raise PatchApplyError("No file patch found in unified diff")

    targets: list[tuple[Path, str | None]] = []
    for fp in patches:
        rel = _resolve_target_path(fp, default_file_path)
        old_content = None
        path = (repo / rel).resolve()
        if path.exists():
            old_content = path.read_text(encoding="utf-8", errors="surrogateescape")
        new_content = _apply_file_patch(old_content, fp, rel)
        targets.append((path, new_content))

    # apply atomically after validation
    touched: list[str] = []
    for path, content in targets:
        if content is None:
            if path.exists():
                path.unlink()
                touched.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        touched.append(str(path))
    return touched


def parse_unified_diff(diff_text: str) -> list[FilePatch]:
    text = diff_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    patches: list[FilePatch] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("--- "):
            i += 1
            continue
        old_path = _normalize_patch_path(line[4:].strip().split("\t")[0])
        i += 1
        if i >= len(lines) or not lines[i].startswith("+++ "):
            raise PatchApplyError(f"Malformed diff near file header: {line}")
        new_path = _normalize_patch_path(lines[i][4:].strip().split("\t")[0])
        i += 1
        hunks: list[Hunk] = []
        while i < len(lines):
            cur = lines[i]
            if cur.startswith("--- "):
                break
            m = _HUNK_RE.match(cur)
            if not m:
                i += 1
                continue
            old_start = int(m.group(1))
            old_count = int(m.group(2) or "1")
            new_start = int(m.group(3))
            new_count = int(m.group(4) or "1")
            i += 1
            hunk_lines: list[str] = []
            while i < len(lines):
                hl = lines[i]
                if _HUNK_RE.match(hl) or hl.startswith("--- "):
                    break
                if hl.startswith((" ", "+", "-", "\\")):
                    hunk_lines.append(hl)
                i += 1
            hunks.append(Hunk(old_start=old_start, old_count=old_count, new_start=new_start, new_count=new_count, lines=hunk_lines))
        patches.append(FilePatch(old_path=old_path, new_path=new_path, hunks=hunks))
    return patches


def _normalize_patch_path(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _resolve_target_path(file_patch: FilePatch, default_file_path: str | None) -> str:
    if file_patch.new_path != "/dev/null":
        return file_patch.new_path
    if file_patch.old_path != "/dev/null":
        return file_patch.old_path
    if default_file_path:
        return default_file_path
    raise PatchApplyError("Unable to determine target file path for patch")


def _apply_file_patch(existing: str | None, file_patch: FilePatch, display_path: str) -> str | None:
    if file_patch.new_path == "/dev/null":
        # delete file
        if existing is None:
            raise PatchApplyError(f"{display_path}: cannot delete missing file")
        return None

    current = existing if existing is not None else ""
    src_lines = current.splitlines(keepends=True)
    out_lines: list[str] = []
    src_idx = 0

    for h_idx, hunk in enumerate(file_patch.hunks, start=1):
        target_idx = max(hunk.old_start - 1, 0)
        if target_idx < src_idx:
            raise PatchApplyError(f"{display_path}: overlapping hunk #{h_idx}")
        out_lines.extend(src_lines[src_idx:target_idx])
        src_idx = target_idx
        for lno, hline in enumerate(hunk.lines, start=1):
            if not hline:
                continue
            tag = hline[0]
            payload = hline[1:]
            if tag == "\\":
                continue
            if tag == " ":
                _expect_line(src_lines, src_idx, payload, display_path, h_idx, lno, "context")
                out_lines.append(src_lines[src_idx])
                src_idx += 1
            elif tag == "-":
                _expect_line(src_lines, src_idx, payload, display_path, h_idx, lno, "remove")
                src_idx += 1
            elif tag == "+":
                out_lines.append(payload + "\n")
            else:
                raise PatchApplyError(f"{display_path}: unsupported hunk line tag {tag!r} in hunk #{h_idx}")

    out_lines.extend(src_lines[src_idx:])
    return "".join(out_lines)


def _expect_line(src_lines: list[str], index: int, expected: str, display_path: str, hunk_index: int, line_index: int, kind: str) -> None:
    if index >= len(src_lines):
        raise PatchApplyError(f"{display_path}: hunk #{hunk_index} {kind} line {line_index} out of bounds")
    actual = src_lines[index].rstrip("\n").rstrip("\r")
    if actual != expected:
        raise PatchApplyError(
            f"{display_path}: hunk #{hunk_index} {kind} mismatch at source line {index + 1}; expected {expected!r}, got {actual!r}"
        )
