from villani_code.orchestrate.worker import parse_worker_report


def test_parse_worker_report_json_block() -> None:
    output = 'hello\nWORKER_REPORT_JSON\n{"status":"success","summary":"ok","files_changed":["a.py"]}'
    report = parse_worker_report(output)
    assert report.status == "success"
    assert report.files_changed == ["a.py"]


def test_parse_worker_report_fallback_on_malformed() -> None:
    output = "WORKER_REPORT_JSON\n{ bad json\n$ pytest -q\nsrc/app.py"
    report = parse_worker_report(output)
    assert report.status in {"partial", "failed"}
    assert "pytest -q" in report.commands_run
    assert "src/app.py" in report.files_read


def test_run_worker_passes_through_flags(monkeypatch, tmp_path) -> None:
    from villani_code.orchestrate.worker import WorkerConfig, run_worker

    captured = {}

    class DummyProc:
        returncode = 0
        stdout = "WORKER_REPORT_JSON\n{}"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return DummyProc()

    monkeypatch.setattr("subprocess.run", fake_run)
    cfg = WorkerConfig(
        base_url="http://localhost:8000",
        model="m",
        provider="openai",
        api_key="k",
        timeout_seconds=5,
        max_tokens=123,
        stream=True,
        thinking='{"x":1}',
        unsafe=True,
        verbose=True,
        extra_json='{"y":2}',
        redact=True,
        dangerously_skip_permissions=True,
        auto_accept_edits=True,
        auto_approve=False,
        plan_mode="strict",
        max_repair_attempts=4,
        small_model=True,
        debug="trace",
        debug_dir=tmp_path / "dbg",
    )
    run_worker(repo=tmp_path, prompt="p", config=cfg)
    cmd = captured["cmd"]
    assert "--max-tokens" in cmd and "123" in cmd
    assert "--stream" in cmd
    assert "--unsafe" in cmd
    assert "--verbose" in cmd
    assert "--redact" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--auto-accept-edits" in cmd
    assert "--small-model" in cmd
    assert "--auto-approve" not in cmd
    assert "--debug" in cmd and "trace" in cmd
