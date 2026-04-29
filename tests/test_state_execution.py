from __future__ import annotations

from villani_code.state_execution import collect_validation_artifacts


def test_collect_validation_artifacts_ignores_masked_failure_patterns() -> None:
    transcript = {
        "tool_results": [
            {
                "content": '{"command":"python web_app.py || echo ok","exit_code":0}',
                "is_error": False,
            }
        ]
    }
    artifacts = collect_validation_artifacts(transcript)
    assert artifacts == []


def test_collect_validation_artifacts_keeps_clean_command() -> None:
    transcript = {
        "tool_results": [
            {
                "content": '{"command":"python web_app.py","exit_code":0}',
                "is_error": False,
            }
        ]
    }
    artifacts = collect_validation_artifacts(transcript)
    assert artifacts == ["python web_app.py (exit=0)"]


def test_collect_validation_artifacts_skips_error_results_even_with_command_shape() -> None:
    transcript = {
        "tool_results": [
            {
                "content": '{"command":"python app.py > out.txt 2>&1 & echo STARTED","exit_code":0}',
                "is_error": True,
            }
        ]
    }
    artifacts = collect_validation_artifacts(transcript)
    assert artifacts == []
