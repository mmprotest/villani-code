from pathlib import Path
import subprocess

from villani_code import state_runtime


class Dummy:
    def __init__(self, repo: Path):
        self.repo = repo
        self._provisional_scratch_candidates = set()
        self._explicit_mutation_created_paths = set()
        self._cleanup_summary = {}
        self._execution_plan = None
        self.event_callback = lambda *_args, **_kwargs: None


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)


def test_bash_created_untracked_removed_when_validation_passes(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("ok\n")
    (tmp_path / "src").mkdir()
    runner = Dummy(tmp_path)
    f = tmp_path / "probe.txt"
    f.write_text("x")
    runner._provisional_scratch_candidates.add("probe.txt")

    monkeypatch.setattr(state_runtime, "run_validation", lambda *a, **k: type("R", (), {"passed": True})())
    state_runtime.cleanup_provisional_scratch_artifacts(runner, ["README.md"])
    assert not f.exists()
    assert runner._cleanup_summary["cleanup_kept"] is True


def test_bash_created_untracked_restored_when_validation_fails(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("ok\n")
    (tmp_path / "src").mkdir()
    runner = Dummy(tmp_path)
    f = tmp_path / "probe.txt"
    f.write_text("x")
    runner._provisional_scratch_candidates.add("probe.txt")
    monkeypatch.setattr(state_runtime, "run_validation", lambda *a, **k: type("R", (), {"passed": False})())
    state_runtime.cleanup_provisional_scratch_artifacts(runner, ["README.md"])
    assert f.exists()
    assert runner._cleanup_summary["cleanup_restored"] is True


def test_write_created_never_candidate(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("ok\n")
    runner = Dummy(tmp_path)
    f = tmp_path / "probe.txt"
    f.write_text("x")
    runner._provisional_scratch_candidates.add("probe.txt")
    runner._explicit_mutation_created_paths.add("probe.txt")
    monkeypatch.setattr(state_runtime, "run_validation", lambda *a, **k: type("R", (), {"passed": True})())
    state_runtime.cleanup_provisional_scratch_artifacts(runner, ["README.md"])
    assert f.exists()


def test_src_and_tests_never_removed(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "src").mkdir(); (tmp_path / "tests").mkdir()
    (tmp_path / "README.md").write_text("ok\n")
    sf = tmp_path / "src" / "probe.txt"; sf.write_text("x")
    tf = tmp_path / "tests" / "probe.txt"; tf.write_text("x")
    runner = Dummy(tmp_path)
    runner._provisional_scratch_candidates.update({"src/probe.txt", "tests/probe.txt"})
    monkeypatch.setattr(state_runtime, "run_validation", lambda *a, **k: type("R", (), {"passed": True})())
    state_runtime.cleanup_provisional_scratch_artifacts(runner, ["README.md"])
    assert sf.exists() and tf.exists()
