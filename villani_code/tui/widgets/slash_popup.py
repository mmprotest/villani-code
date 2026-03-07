from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView, Static

from villani_code.tui.components.command_palette import PaletteItem


class SlashCommandPopup(Vertical):
    def __init__(self) -> None:
        super().__init__(id="slash-popup")
        self._items: list[PaletteItem] = []

    def compose(self) -> ComposeResult:
        yield Static("Slash commands", id="slash-popup-title")
        yield ListView(id="slash-popup-list")

    def on_mount(self) -> None:
        self.display = False

    @property
    def visible(self) -> bool:
        return self.display and bool(self._items)

    @property
    def has_items(self) -> bool:
        return bool(self._items)

    def set_suggestions(self, items: list[PaletteItem]) -> None:
        self._items = items
        options = self._options()
        options.clear()
        for item in items:
            options.append(ListItem(Label(f"{item.trigger} — {item.description}")))
        if items:
            self.display = True
            options.index = 0
            self._sync_selected_style()
        else:
            self.hide_popup()

    def hide_popup(self) -> None:
        self.display = False
        self._items = []
        self._options().clear()

    def _options(self) -> ListView:
        options = self.query_one("#slash-popup-list", ListView)
        options.can_focus = False
        return options

    def _sync_selected_style(self) -> None:
        options = self._options()
        for idx, child in enumerate(options.children):
            if isinstance(child, ListItem):
                child.set_class(idx == options.index, "selected")

    def cursor_up(self) -> None:
        if not self.visible:
            return
        self._options().action_cursor_up()
        self._sync_selected_style()

    def cursor_down(self) -> None:
        if not self.visible:
            return
        self._options().action_cursor_down()
        self._sync_selected_style()

    def selected_item(self) -> PaletteItem | None:
        if not self.visible:
            return None
        index = self._options().index
        if index is None or index < 0 or index >= len(self._items):
            return None
        return self._items[index]

    def accept_selected_trigger(self) -> str | None:
        selected = self.selected_item()
        if selected is None:
            return None
        return selected.trigger

    def on_list_view_highlighted(self, _event: ListView.Highlighted) -> None:
        if self.visible:
            self._sync_selected_style()
