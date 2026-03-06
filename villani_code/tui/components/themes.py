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
        prompt_toolkit_style=Style.from_dict(
            {
                "bottom-toolbar": "bg:#202020 #d0d0d0",
                "prompt": "#00afff bold",
                "input-field": "#f0f0f0",
                "approval.label": "bg:#3a2f00 #ffd866 bold",
                "approval.yes": "bg:#1f3b2b #a6e22e bold",
                "approval.always": "bg:#1f2e3b #66d9ef bold",
                "approval.no": "bg:#3b1f1f #ff6188 bold",
                "approval.active": "bg:#00afff #000000 bold",
                "banner": "#66d9ef",
                "banner.model": "italic #a0a0a0",
            }
        ),
        rich_name="monokai",
        spacing=1,
    ),
    "high-contrast": ThemeSpec(
        prompt_toolkit_style=Style.from_dict(
            {
                "bottom-toolbar": "bg:#ffffff #000000",
                "prompt": "#ffff00 bold",
                "input-field": "#000000",
                "approval.label": "bg:#000000 #ffff00 bold",
                "approval.yes": "bg:#000000 #00ff00 bold",
                "approval.always": "bg:#000000 #00ffff bold",
                "approval.no": "bg:#000000 #ff005f bold",
                "approval.active": "bg:#0000ff #ffffff bold",
                "banner": "#005f87",
                "banner.model": "#444444",
            }
        ),
        rich_name="ansi_light",
        spacing=1,
    ),
}


def get_theme(name: str) -> ThemeSpec:
    return THEMES.get(name, THEMES["default"])
