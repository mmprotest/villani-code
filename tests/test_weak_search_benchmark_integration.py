from villani_code.benchmark.models import BenchmarkRunResult, BenchmarkTrack, TaskDifficulty, TaskFamily, TaskSource, FairnessClassification
from villani_code.benchmark.reporting import aggregate_results


def _row(**kwargs):
    base = dict(
        task_id="t1", task_family=TaskFamily.BUGFIX, task_difficulty=TaskDifficulty.EASY, task_language="python", task_checksum="x",
        agent_name="a", adapter_name="villani", adapter_version="1", adapter_capability="x", fairness_classification=FairnessClassification.EXACT_COMPARABLE,
        fairness_notes="n", telemetry_capability="t", model_name="m", success=1, visible_pass=True, hidden_pass=True, runtime_seconds=1.0, timeout=False,
        touched_file_paths=[], files_touched=0, lines_added=0, lines_deleted=0, verifications_run=[], benchmark_track=BenchmarkTrack.CORE, task_source_type=TaskSource.CURATED,
    )
    base.update(kwargs)
    return BenchmarkRunResult(**base)


def test_benchmark_telemetry_extension_backwards_compatible():
    row = _row(weak_search_cycles=3, branches_pruned=1, candidate_patches_verified=2, no_progress_stop=False)
    payload = aggregate_results([row])
    assert "avg_weak_search_cycles" in payload


def test_benchmark_mode_routing_uses_weak_search(tmp_path):
    from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
    from villani_code.state import Runner

    class DummyClient:
        pass

    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, benchmark_config=BenchmarkRuntimeConfig(enabled=True), runtime="weak-search")
    out = runner.run("fix bug")
    assert "weak_search" in out
