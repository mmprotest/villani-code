from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.styles import Style


@dataclass(frozen=True)
class ThemeSpec:
    prompt_toolkit_style: Style
    rich_name: str
    spacing: int = 1


THEMES: dict[str, ThemeSpec] = {
    "default": ThemeSpec(
        prompt_toolkit_style=Style.from_dict({"bottom-toolbar": "bg:#202020 #d0d0d0", "prompt": "#00afff bold"}),
        rich_name="monokai",
        spacing=1,
    ),
    "high-contrast": ThemeSpec(
        prompt_toolkit_style=Style.from_dict({"bottom-toolbar": "bg:#ffffff #000000", "prompt": "#ffff00 bold"}),
        rich_name="ansi_light",
        spacing=1,
    ),
}


def get_theme(name: str) -> ThemeSpec:
    return THEMES.get(name, THEMES["default"])
