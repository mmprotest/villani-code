from pathlib import Path

from villani_code.state import Runner


class DummyClient:
    def create_message(self, payload, stream=False):
        return {"role": "assistant", "content": []}


class DummyMap:
    source_roots = ["src"]
    test_roots = ["tests"]
    package_roots = []


def _mk_runner(tmp_path: Path) -> Runner:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    r = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False)  # type: ignore[arg-type]
    r._repo_map = DummyMap()
    r._provisional_scratch_candidates = set()
    r._non_scratch_created_files = set()
    return r


def test_cleanup_removes_bash_created_file_when_verification_passes(tmp_path: Path) -> None:
    runner = _mk_runner(tmp_path)
    f = tmp_path / "tmp_probe.txt"
    f.write_text("x", encoding="utf-8")
    runner._provisional_scratch_candidates.add("tmp_probe.txt")
    runner._run_verification = lambda trigger="": "status: PASS"  # type: ignore[assignment]
    runner._cleanup_provisional_scratch_after_success("Validation: passed.")
    assert not f.exists()
    assert runner._cleanup_telemetry["cleanup_kept"] is True


def test_cleanup_restores_when_verification_fails(tmp_path: Path) -> None:
    runner = _mk_runner(tmp_path)
    f = tmp_path / "tmp_probe.txt"
    f.write_text("x", encoding="utf-8")
    runner._provisional_scratch_candidates.add("tmp_probe.txt")
    runner._run_verification = lambda trigger="": "status: FAIL"  # type: ignore[assignment]
    runner._cleanup_provisional_scratch_after_success("Validation: passed.")
    assert f.exists()
    assert f.read_text(encoding="utf-8") == "x"
    assert runner._cleanup_telemetry["cleanup_restored"] is True


def test_cleanup_never_removes_src_or_tests(tmp_path: Path) -> None:
    runner = _mk_runner(tmp_path)
    in_src = tmp_path / "src" / "tmp.py"
    in_test = tmp_path / "tests" / "tmp.py"
    in_src.write_text("x", encoding="utf-8")
    in_test.write_text("x", encoding="utf-8")
    runner._provisional_scratch_candidates.update({"src/tmp.py", "tests/tmp.py"})
    runner._run_verification = lambda trigger="": "status: PASS"  # type: ignore[assignment]
    runner._cleanup_provisional_scratch_after_success("Validation: passed.")
    assert in_src.exists()
    assert in_test.exists()
