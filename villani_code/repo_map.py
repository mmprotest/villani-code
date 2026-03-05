from __future__ import annotations

from collections import Counter, defaultdict

from villani_code.indexing import RepoIndex


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
