from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from villani_code.indexing import RepoIndex
from villani_code.repo_rules import is_ignored_repo_path


@dataclass(slots=True)
class RepoMap:
    packages: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    ci_files: list[str] = field(default_factory=list)
    tool_configs: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    inferred_commands: list[str] = field(default_factory=list)
    suspicious_files: list[str] = field(default_factory=list)
    key_modules: list[str] = field(default_factory=list)
    todo_hits: list[str] = field(default_factory=list)
    import_hotspots: list[str] = field(default_factory=list)
    doc_commands: list[str] = field(default_factory=list)


def build_repo_map(index: RepoIndex, max_chars: int = 8000) -> str:
    files = sorted(index.iter_files(), key=lambda f: f.path)
    tree = _tree_lines([f.path for f in files], depth=3)
    modules = Counter(path.split("/")[0] for path in (f.path for f in files) if "/" in path)
    important = sorted(files, key=lambda f: (-len(f.symbols), -f.size, f.path))[:30]

    lines: list[str] = ["repo tree (depth<=3):", *tree, "", "top modules by file count:"]
    for module, count in sorted(modules.items(), key=lambda item: (-item[1], item[0]))[:10]:
        lines.append(f"- {module}: {count}")
    lines.append("")
    lines.append("important files:")
    for fi in important:
        symbols = ", ".join(fi.symbols[:8]) if fi.symbols else "-"
        lines.append(f"- {fi.path} :: {symbols}")

    text = "\n".join(lines)
    return text[:max_chars]


def build_structured_repo_map(repo: Path) -> RepoMap:
    repo = repo.resolve()
    files = sorted(
        p.relative_to(repo).as_posix()
        for p in repo.rglob("*")
        if p.is_file() and not is_ignored_repo_path(p.relative_to(repo).as_posix())
    )
    map_obj = RepoMap()
    for rel in files:
        lower = rel.lower()
        name = Path(rel).name
        if rel.endswith("/__init__.py"):
            map_obj.packages.append(str(Path(rel).parent).replace("\\", "/"))
        if rel.startswith("tests/") or name.startswith("test_"):
            map_obj.tests.append(rel)
        if rel.endswith(".md") or rel.startswith("docs/"):
            map_obj.docs.append(rel)
        if rel.endswith((".toml", ".yaml", ".yml", ".json", ".ini", ".cfg")):
            map_obj.config_files.append(rel)
        if ".github/workflows/" in rel:
            map_obj.ci_files.append(rel)
        if name in {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "tox.ini", ".ruff.toml", "mypy.ini", ".flake8"}:
            map_obj.tool_configs.append(rel)
        if rel.startswith(("scripts/", "bin/")) or name.endswith((".sh", ".bash")):
            map_obj.scripts.append(rel)
        if name in {"main.py", "cli.py"} or "__main__.py" in rel:
            map_obj.entrypoints.append(rel)
        if lower.endswith((".bak", ".tmp")) or "generated" in lower:
            map_obj.suspicious_files.append(rel)
        if rel.endswith(".py") and not rel.startswith("tests/"):
            map_obj.key_modules.append(rel)

    map_obj.packages = sorted(set(map_obj.packages))
    map_obj.entrypoints = sorted(set(map_obj.entrypoints))
    map_obj.tests = sorted(set(map_obj.tests))
    map_obj.docs = sorted(set(map_obj.docs))
    map_obj.config_files = sorted(set(map_obj.config_files))
    map_obj.ci_files = sorted(set(map_obj.ci_files))
    map_obj.tool_configs = sorted(set(map_obj.tool_configs))
    map_obj.scripts = sorted(set(map_obj.scripts))
    map_obj.suspicious_files = sorted(set(map_obj.suspicious_files))

    map_obj.inferred_commands = _infer_commands(map_obj)
    map_obj.todo_hits = _find_todo_hits(repo, files)
    map_obj.doc_commands = _extract_docs_commands(repo, map_obj.docs)
    map_obj.import_hotspots = _import_hotspots(repo, map_obj.key_modules)
    map_obj.key_modules = sorted(map_obj.key_modules)[:30]
    return map_obj


def _infer_commands(map_obj: RepoMap) -> list[str]:
    cmds: list[str] = []
    if map_obj.tests:
        cmds.append("pytest -q")
    if "pyproject.toml" in map_obj.tool_configs:
        cmds.append("python -m pip install -e .")
    if map_obj.entrypoints:
        cmds.append("python -m " + Path(map_obj.entrypoints[0]).stem)
    if any("README.md" == Path(d).name for d in map_obj.docs):
        cmds.append("python -m villani_code.cli --help")
    return cmds


def _find_todo_hits(repo: Path, files: list[str]) -> list[str]:
    hits: list[str] = []
    for rel in files:
        if len(hits) >= 20:
            break
        if not rel.endswith((".py", ".md", ".txt", ".toml")):
            continue
        path = repo / rel
        try:
            for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if any(marker in line for marker in ("TODO", "FIXME", "HACK", "XXX")):
                    hits.append(f"{rel}:{idx}:{line.strip()[:100]}")
                    break
        except OSError:
            continue
    return hits


def _extract_docs_commands(repo: Path, docs: list[str]) -> list[str]:
    commands: list[str] = []
    for rel in docs[:10]:
        path = repo / rel
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("$ ", "python ", "pytest ")):
                commands.append(stripped.lstrip("$ "))
                if len(commands) >= 10:
                    return commands
    return commands


def _import_hotspots(repo: Path, modules: list[str]) -> list[str]:
    counts: list[tuple[str, int]] = []
    for rel in modules:
        path = repo / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        import_count = sum(1 for line in text.splitlines() if line.strip().startswith(("import ", "from ")))
        if import_count >= 4:
            counts.append((rel, import_count))
    counts.sort(key=lambda item: (-item[1], item[0]))
    return [f"{rel}:{count}" for rel, count in counts[:8]]


def _tree_lines(paths: list[str], depth: int) -> list[str]:
    tree: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        parts = path.split("/")
        for i in range(min(len(parts) - 1, depth)):
            parent = "/".join(parts[:i])
            tree[parent].add(parts[i])
    lines: list[str] = []

    def walk(parent: str, level: int) -> None:
        if level >= depth:
            return
        for name in sorted(tree.get(parent, set())):
            prefix = "  " * level
            lines.append(f"{prefix}- {name}/")
            child = f"{parent}/{name}" if parent else name
            walk(child, level + 1)

    walk("", 0)
    return lines
