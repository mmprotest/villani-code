from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    mtime: float
    lang: str
    symbols: list[str]
    snippet: str


@dataclass(frozen=True)
class IgnoreRules:
    names: set[str]
    suffixes: set[str]

    def should_ignore(self, path: Path) -> bool:
        return any(part in self.names for part in path.parts) or path.suffix in self.suffixes


DEFAULT_IGNORE = IgnoreRules(
    names={".git", ".venv", "node_modules", "__pycache__", ".villani_code", ".pytest_cache"},
    suffixes={".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".lock", ".bin"},
)


LANG_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "shell",
}

SYMBOL_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"^\s*def\s+([A-Za-z_][\w]*)\s*\(", re.MULTILINE),
        re.compile(r"^\s*class\s+([A-Za-z_][\w]*)\s*[:(]", re.MULTILINE),
    ],
    "javascript": [re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("), re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\(?[^=]*=>")],
    "typescript": [re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("), re.compile(r"\b(?:interface|type|class)\s+([A-Za-z_$][\w$]*)\b")],
    "go": [re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\s*\(", re.MULTILINE), re.compile(r"^\s*type\s+([A-Za-z_][\w]*)\s+", re.MULTILINE)],
    "rust": [re.compile(r"\bfn\s+([A-Za-z_][\w]*)\s*\("), re.compile(r"\b(?:struct|enum|trait|impl)\s+([A-Za-z_][\w]*)\b")],
}


class RepoIndex:
    def __init__(self, root: Path, files: list[FileInfo], fingerprint: str):
        self.root = root
        self.files = files
        self.fingerprint = fingerprint

    @classmethod
    def build(cls, root: Path, ignore: IgnoreRules = DEFAULT_IGNORE) -> "RepoIndex":
        root = root.resolve()
        files: list[FileInfo] = []
        fingerprints: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if ignore.should_ignore(rel):
                continue
            stat = path.stat()
            size = stat.st_size
            if size > 2_000_000:
                continue
            snippet = extract_snippet(path)
            lang = guess_language(path)
            symbols = extract_symbols(snippet, lang)
            files.append(FileInfo(path=rel.as_posix(), size=size, mtime=stat.st_mtime, lang=lang, symbols=symbols, snippet=snippet))
            fingerprints.append(f"{rel.as_posix()}:{size}:{int(stat.st_mtime)}")
        digest = hashlib.sha256("\n".join(fingerprints).encode("utf-8")).hexdigest()
        return cls(root=root, files=files, fingerprint=digest)

    @classmethod
    def load(cls, path: Path) -> "RepoIndex":
        payload = json.loads(path.read_text(encoding="utf-8"))
        root = Path(payload["root"])
        files = [FileInfo(**item) for item in payload.get("files", [])]
        return cls(root=root, files=files, fingerprint=payload.get("fingerprint", ""))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"root": str(self.root), "fingerprint": self.fingerprint, "files": [asdict(item) for item in self.files]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def needs_rebuild(self, root: Path) -> bool:
        return self.fingerprint != compute_repo_fingerprint(root.resolve())

    def iter_files(self) -> Iterable[FileInfo]:
        return iter(self.files)


def compute_repo_fingerprint(root: Path, ignore: IgnoreRules = DEFAULT_IGNORE) -> str:
    markers: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if ignore.should_ignore(rel):
            continue
        stat = path.stat()
        markers.append(f"{rel.as_posix()}:{stat.st_size}:{int(stat.st_mtime)}")
    return hashlib.sha256("\n".join(markers).encode("utf-8")).hexdigest()


def guess_language(path: Path) -> str:
    return LANG_BY_SUFFIX.get(path.suffix.lower(), "text")


def extract_symbols(text: str, lang: str, limit: int = 64) -> list[str]:
    symbols: list[str] = []
    for pattern in SYMBOL_PATTERNS.get(lang, []):
        for match in pattern.finditer(text):
            symbol = match.group(1)
            if symbol not in symbols:
                symbols.append(symbol)
            if len(symbols) >= limit:
                return symbols
    return symbols


def extract_snippet(path: Path, max_lines: int = 40, max_bytes: int = 8_000) -> str:
    raw = path.read_bytes()[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()[:max_lines]
    return "\n".join(lines)
