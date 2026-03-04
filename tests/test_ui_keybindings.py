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
