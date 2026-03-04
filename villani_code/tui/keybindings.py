from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.key_binding import KeyBindings

from villani_code.tui.state import ActiveModal, UIState


def build_keybindings(state: UIState, call_async: Callable[[str], None], open_output: Callable[[], None], toggle_fold: Callable[[], None]) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("c-p")
    def _palette(_event):
        state.active_modal = ActiveModal.PALETTE

    @kb.add("c-d")
    def _diff(_event):
        state.show_diff = not state.show_diff

    @kb.add("c-t")
    def _tasks(_event):
        state.show_tasks = not state.show_tasks

    @kb.add("c-f")
    def _focus(_event):
        state.focus_mode = not state.focus_mode
        if state.focus_mode:
            state.show_tasks = False
            state.show_diff = False
            state.verbose_tool_output = False

    @kb.add("c-o")
    def _verbose(_event):
        state.verbose_tool_output = not state.verbose_tool_output

    @kb.add("c-s")
    def _save(_event):
        call_async("checkpoint")

    @kb.add("c-_")
    def _help(_event):
        state.active_modal = ActiveModal.HELP

    @kb.add("escape")
    def _esc(_event):
        state.active_modal = ActiveModal.NONE

    @kb.add("e")
    def _expand(_event):
        open_output()

    @kb.add("enter")
    def _toggle_fold(_event):
        toggle_fold()

    return kb
