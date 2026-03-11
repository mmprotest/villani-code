from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.controller import WeakSearchController


def test_repro_tasks_use_repro_command_as_target_verifier(tmp_path):
    class DummyRunner:
        def __init__(self):
            self.repo = tmp_path
            self.benchmark_config = BenchmarkRuntimeConfig(enabled=True, task_id="repro_case", visible_verification=["python repro.py"], allowlist_paths=["src/"], expected_files=["src/a.py"])
            self.event_callback = lambda _e: None

    controller = WeakSearchController(DummyRunner(), "fix repro")
    evidence = controller._collect_evidence()
    assert evidence.repro_commands == ["python repro.py"]
