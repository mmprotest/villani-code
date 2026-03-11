from villani_code.runtime.blackboard import BlackboardStore
from villani_code.runtime.schemas import Blackboard, RuntimeBudgets


def test_blackboard_creation_and_schema_basics(tmp_path):
    store = BlackboardStore(tmp_path, "run1")
    board = Blackboard(run_id="run1", task_id="t1", objective="fix", repo_root=str(tmp_path), budgets=RuntimeBudgets())
    store.write(board)
    assert store.blackboard_path.exists()
    payload = store.blackboard_path.read_text()
    assert '"run_id": "run1"' in payload
    assert store.attempts_dir.exists()
