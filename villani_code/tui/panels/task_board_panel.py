from __future__ import annotations

from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame

from ui.task_board import TaskManager


class TaskBoardPanel:
    def __init__(self, task_manager: TaskManager) -> None:
        self.task_manager = task_manager
        self.show_timeline = False
        self.control = FormattedTextControl(self._render)
        self.container = Frame(Window(content=self.control, wrap_lines=False), title="Task Board")

    def toggle_section(self) -> None:
        self.show_timeline = not self.show_timeline

    def _render(self):
        rows = []
        if self.show_timeline:
            rows.append(("class:panel.header", "Timeline\n"))
            for event in self.task_manager.recent_events(20):
                rows.append(("", f"{event.kind}: {event.detail}\n"))
        else:
            rows.append(("class:panel.header", "Tasks\n"))
            for task in self.task_manager.tasks.values():
                rows.append(("", f"{task.title:<20} {task.status.value:<12} {int(task.progress*100)}%\n"))
        return rows
