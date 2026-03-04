from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CommandAction:
    id: str
    title: str
    category: str
    target: str
    payload: dict[str, str] | None = None


@dataclass(slots=True)
class PaletteItem:
    trigger: str
    description: str
    action: CommandAction


class CommandPalette:
    def __init__(self) -> None:
        self.items = self._default_items()

    def _default_items(self) -> list[PaletteItem]:
        data = [
            ("/help", "full command reference", "command", "help"),
            ("/tasks", "task board", "command", "tasks"),
            ("/settings", "settings panel", "command", "settings"),
            ("/diff", "open diff viewer", "command", "diff"),
            ("/rewind", "restore last checkpoint", "command", "rewind"),
            ("/export", "export session JSON", "command", "export"),
            ("/fork", "fork session", "command", "fork"),
            ("toggle verbose", "toggle verbose", "action", "toggle_verbose"),
            ("save checkpoint", "save checkpoint", "action", "save_checkpoint"),
            ("show diff", "show diff", "action", "show_diff"),
            ("focus mode", "focus mode", "action", "focus_mode"),
        ]
        return [
            PaletteItem(trigger=t, description=d, action=CommandAction(id=t, title=t, category=c, target=target))
            for t, d, c, target in data
        ]

    def search(self, query: str, limit: int = 8) -> list[tuple[int, PaletteItem]]:
        query = query.strip().lower()
        scored: list[tuple[int, PaletteItem]] = []
        for item in self.items:
            haystack = f"{item.trigger} {item.description}".lower()
            score = fuzzy_score(query, haystack)
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:limit]

    def resolve(self, query: str) -> CommandAction | None:
        matches = self.search(query, limit=1)
        if not matches:
            return None
        return matches[0][1].action


def fuzzy_score(query: str, text: str) -> int:
    if not query:
        return 1
    if query in text:
        return 100 - (text.index(query) // 2)
    qi = 0
    points = 0
    for i, char in enumerate(text):
        if qi < len(query) and char == query[qi]:
            points += 3
            if i > 0 and text[i - 1] in " /-":
                points += 2
            qi += 1
    if qi != len(query):
        return 0
    return points
