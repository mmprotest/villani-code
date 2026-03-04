from ui.task_board import TaskManager, TaskStatus
from villani_code.tui.panels.task_board_panel import TaskBoardPanel


def test_task_transitions_and_timeline() -> None:
    mgr = TaskManager()
    mgr.create_task("1", "Run tool")
    mgr.update_status("1", TaskStatus.IN_PROGRESS, 0.3)
    mgr.update_status("1", TaskStatus.COMPLETED, 1.0)
    events = mgr.recent_events()
    assert mgr.tasks["1"].status == TaskStatus.COMPLETED
    assert len(events) >= 3


def test_task_panel_renders_updates() -> None:
    mgr = TaskManager()
    panel = TaskBoardPanel(mgr)
    mgr.create_task("1", "Task A")
    text = "".join(t[1] for t in panel._render())
    assert "Task A" in text
    panel.toggle_section()
    text2 = "".join(t[1] for t in panel._render())
    assert "Timeline" in text2
