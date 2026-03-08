from __future__ import annotations

import json
from pathlib import Path

import yaml

from villani_code.benchmark.health import run_healthcheck
from villani_code.benchmark.task_loader import load_task


def _write_task(root: Path, name: str, track: str = "core") -> None:
    t = root / name
    (t / "repo" / "src").mkdir(parents=True)
    (t / "prompt.txt").write_text("fix", encoding="utf-8")
    (t / "metadata.json").write_text(json.dumps({"expected_files": ["src/a.py"], "primary_skill": "debug"}), encoding="utf-8")
    (t / "repo" / "src" / "a.py").write_text("x=1\n", encoding="utf-8")
    (t / "task.yaml").write_text(
        yaml.safe_dump(
            {
                "id": name,
                "benchmark_track": track,
                "family": "bugfix",
                "difficulty": "easy",
                "language": "python",
                "max_minutes": 1,
                "max_files_touched": 2,
                "expected_artifacts": ["patch"],
                "visible_verification": ["python -c 'print(1)'"] ,
                "hidden_verification": ["python -c 'print(1)'"] ,
                "success_policy": {
                    "require_visible_pass": True,
                    "require_hidden_pass": True,
                    "fail_on_timeout": True,
                    "fail_on_repo_dirty_outside_allowlist": True,
                },
                "allowlist_paths": ["src/"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_healthcheck_catches_duplicate_ids_and_hidden_leak(tmp_path: Path) -> None:
    _write_task(tmp_path, "t1")
    _write_task(tmp_path, "t2")
    # duplicate id
    data = yaml.safe_load((tmp_path / "t2" / "task.yaml").read_text())
    data["id"] = "t1"
    (tmp_path / "t2" / "task.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    # hidden leak
    leak = tmp_path / "t1" / "repo" / "hidden_checks"
    leak.mkdir(parents=True)
    (leak / "secret.txt").write_text("x", encoding="utf-8")

    health = run_healthcheck(tmp_path)
    codes = {e["code"] for e in health["errors"]}
    assert "duplicate_task_id" in codes
    assert "hidden_asset_leak" in codes


def test_explicit_track_is_respected() -> None:
    task = load_task(Path("benchmark_tasks/villani_feature_v1/feature_bugfix_001_datetime_cli"))
    assert task.benchmark_track.value == "feature"
