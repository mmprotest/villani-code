from villani_code.tui.components.task_board import TaskManager, TaskStatus


def test_task_transitions_and_timeline() -> None:
    mgr = TaskManager()
    mgr.create_task("1", "Run tool")
    mgr.update_status("1", TaskStatus.IN_PROGRESS, 0.3)
    mgr.update_status("1", TaskStatus.COMPLETED, 1.0)
    events = mgr.recent_events()
    assert mgr.tasks["1"].status == TaskStatus.COMPLETED
    assert len(events) >= 3
