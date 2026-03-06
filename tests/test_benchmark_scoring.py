from villani_code.benchmark.graders import (
    compute_composite_score,
    compute_forbidden_files_touched,
    compute_unnecessary_files_touched,
)


def test_unnecessary_files_touched_calculation() -> None:
    changed = ["a.py", "b.py", "c.py"]
    expected = ["a.py", "b.py"]
    assert compute_unnecessary_files_touched(changed, expected) == ["c.py"]


def test_forbidden_files_touched_penalty_input() -> None:
    changed = ["safe.py", "forbidden.py"]
    forbidden = ["forbidden.py", "other.py"]
    assert compute_forbidden_files_touched(changed, forbidden) == ["forbidden.py"]


def test_composite_score_penalizes_forbidden_and_catastrophic() -> None:
    score = compute_composite_score(
        task_success=True,
        forbidden_files_touched_count=1,
        unnecessary_files_touched_count=2,
        catastrophic_failure=True,
        elapsed_seconds=50,
    )
    assert score == 25.0
