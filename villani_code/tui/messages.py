from __future__ import annotations

from textual.message import Message


class LogAppend(Message):
    def __init__(self, text: str) -> None:
        self.text = text
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
