from __future__ import annotations

try:
    from textual.message import Message
except ModuleNotFoundError as exc:
    if exc.name != "textual":
        raise

    class Message:  # type: ignore[override]
        """Lean-environment fallback so headless imports do not require Textual."""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass


class LogAppend(Message):
    def __init__(self, text: str, kind: str = "meta") -> None:
        self.text = text
        self.kind = kind
        super().__init__()


class StatusUpdate(Message):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class SpinnerState(Message):
    def __init__(self, active: bool, label: str | None = None) -> None:
        self.active = active
        self.label = label
        super().__init__()


class ApprovalRequest(Message):
    def __init__(self, prompt: str, choices: list[str], request_id: str) -> None:
        self.prompt = prompt
        self.choices = choices
        self.request_id = request_id
        super().__init__()


class ApprovalResolved(Message):
    def __init__(self, request_id: str, choice: str) -> None:
        self.request_id = request_id
        self.choice = choice
        super().__init__()
