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
