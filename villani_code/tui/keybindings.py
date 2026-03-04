from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit.key_binding import KeyBindings

from villani_code.tui.state import ActiveModal, UIState


def build_keybindings(
    state: UIState,
    call_async: Callable[[str], None],
    open_output: Callable[[], None],
    toggle_fold: Callable[[], None],
    palette_move: Callable[[int], None] | None = None,
    palette_submit: Callable[[], None] | None = None,
    focus_palette: Callable[[], None] | None = None,
    close_modal: Callable[[], None] | None = None,
    open_approval_background: Callable[[], None] | None = None,
    approve_approval: Callable[[], None] | None = None,
    deny_approval: Callable[[], None] | None = None,
    ctrl_c_pressed: Callable[[], None] | None = None,
) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("c-p")
    def _palette(_event):
        state.active_modal = ActiveModal.PALETTE
        if focus_palette:
            focus_palette()

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
        if state.active_modal == ActiveModal.APPROVAL and deny_approval:
            deny_approval()
            return
        state.active_modal = ActiveModal.NONE
        if close_modal:
            close_modal()

    @kb.add("c-c")
    def _ctrl_c(_event):
        if ctrl_c_pressed:
            ctrl_c_pressed()

    @kb.add("up")
    def _up(_event):
        if state.active_modal == ActiveModal.PALETTE and palette_move:
            palette_move(-1)

    @kb.add("down")
    def _down(_event):
        if state.active_modal == ActiveModal.PALETTE and palette_move:
            palette_move(1)

    @kb.add("enter")
    def _enter(_event):
        if state.active_modal == ActiveModal.PALETTE and palette_submit:
            palette_submit()
            return
        if state.active_modal == ActiveModal.APPROVAL:
            if approve_approval:
                approve_approval()
            return
        _event.current_buffer.validate_and_handle()

    @kb.add("c-b")
    def _background(_event):
        if state.active_modal == ActiveModal.APPROVAL and open_approval_background:
            open_approval_background()

    @kb.add("c-e")
    def _expand(_event):
        open_output()

    @kb.add("c-g")
    def _toggle_fold(_event):
        toggle_fold()

    return kb
