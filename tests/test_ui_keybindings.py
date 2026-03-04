from villani_code.tui.keybindings import build_keybindings
from villani_code.tui.state import UIState


def _binding_strings() -> set[str]:
    state = UIState()
    kb = build_keybindings(state, lambda _cmd: None, lambda: None, lambda: None)
    return {"-".join(str(key) for key in binding.keys) for binding in kb.bindings}


def test_transcript_actions_use_ctrl_shortcuts() -> None:
    keys = _binding_strings()

    assert "Keys.ControlE" in keys
    assert "Keys.ControlG" in keys
    assert "Keys.E" not in keys
    assert "Keys.Enter" not in keys


def test_enter_key_submits_current_buffer_when_palette_closed() -> None:
    state = UIState()
    kb = build_keybindings(state, lambda _cmd: None, lambda: None, lambda: None)
    enter_binding = next(binding for binding in kb.bindings if "Keys.ControlM" in "-".join(str(key) for key in binding.keys))

    class _Buffer:
        def __init__(self) -> None:
            self.called = False

        def validate_and_handle(self) -> None:
            self.called = True

    class _Event:
        def __init__(self, buffer: _Buffer) -> None:
            self.current_buffer = buffer

    buffer = _Buffer()
    enter_binding.handler(_Event(buffer))

    assert buffer.called
