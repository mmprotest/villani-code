from __future__ import annotations

import json
from pathlib import Path

from villani_code.benchmark.failure_taxonomy import classify_failure_taxonomy
from villani_code.benchmark.models import (
    BENCHMARK_VERSION,
    BenchmarkCategory,
    BenchmarkRunResult,
    BenchmarkTrack,
    FailureReason,
    FailureTaxonomy,
    FairnessClassification,
    TaskDifficulty,
    TaskFamily,
    TaskSource,
    TelemetryQuality,
)
from villani_code.benchmark.rebuild import rebuild_results_from_directory
from villani_code.benchmark.reporting import diagnostics, render_summary_table, write_markdown_report, write_results


def test_failure_taxonomy_direct_reason_mappings() -> None:
    taxonomy, detail = classify_failure_taxonomy(success=False, failure_reason=FailureReason.TIMEOUT)
    assert taxonomy is FailureTaxonomy.TIMEOUT
    assert detail is None

    taxonomy, _ = classify_failure_taxonomy(success=False, failure_reason=FailureReason.FORBIDDEN_EDIT)
    assert taxonomy is FailureTaxonomy.FORBIDDEN_EDIT


def test_failure_taxonomy_visible_pass_hidden_fail_becomes_partial_fix() -> None:
    taxonomy, detail = classify_failure_taxonomy(
        success=False,
        failure_reason=FailureReason.HIDDEN_VERIFICATION_FAILED,
        visible_pass=True,
        hidden_pass=False,
    )

    assert taxonomy is FailureTaxonomy.PARTIAL_FIX_MISSED_ACCEPTANCE
    assert detail == "hidden verification failed after visible verification passed"


def test_failure_taxonomy_detects_syntax_breakage_from_verifier_output() -> None:
    taxonomy, detail = classify_failure_taxonomy(
        success=False,
        failure_reason=FailureReason.VISIBLE_VERIFICATION_FAILED,
        verification_output='E   SyntaxError: invalid syntax in src/app.py',
    )

    assert taxonomy is FailureTaxonomy.SYNTAX_BREAKAGE
    assert detail == "syntax error detected in verification output"


def test_failure_taxonomy_flags_missing_command_use_for_command_heavy_tasks() -> None:
    taxonomy, detail = classify_failure_taxonomy(
        success=False,
        failure_reason=FailureReason.VISIBLE_VERIFICATION_FAILED,
        num_shell_commands=0,
        verification_relevant=True,
        task_family=TaskFamily.BUGFIX,
        task_type='config_tooling_repair',
        benchmark_category=BenchmarkCategory.CONFIG_TOOLING_REPAIR,
    )

    assert taxonomy is FailureTaxonomy.FAILED_TO_RUN_CORRECT_COMMAND
    assert detail == "no shell commands executed on config_tooling_repair task"


def test_failure_taxonomy_flags_broad_repo_wandering_without_patch() -> None:
    taxonomy, detail = classify_failure_taxonomy(
        success=False,
        failure_reason=FailureReason.VISIBLE_VERIFICATION_FAILED,
        num_shell_commands=15,
        file_reads=24,
        patch_attempts=0,
        files_touched=0,
    )

    assert taxonomy is FailureTaxonomy.GOT_LOST_IN_REPO
    assert detail == "15 shell commands, 24 file reads with no meaningful patch"


def test_failure_taxonomy_flags_over_editing_for_unexpected_spread() -> None:
    taxonomy, detail = classify_failure_taxonomy(
        success=False,
        failure_reason=FailureReason.VISIBLE_VERIFICATION_FAILED,
        files_touched=5,
        meaningful_expected_paths=['src/app.py'],
        meaningful_unexpected_paths=['src/extra.py', 'tests/test_other.py'],
        expected_files=['src/app.py'],
    )

    assert taxonomy is FailureTaxonomy.OVER_EDITED
    assert detail == "touched 2 meaningful unexpected path(s)"


def test_failure_taxonomy_falls_back_to_unknown_when_evidence_is_weak() -> None:
    taxonomy, detail = classify_failure_taxonomy(
        success=False,
        failure_reason=FailureReason.VISIBLE_VERIFICATION_FAILED,
        num_shell_commands=2,
        files_touched=1,
    )

    assert taxonomy is FailureTaxonomy.UNKNOWN_FAILURE
    assert detail is None


def test_failure_taxonomy_success_is_stable() -> None:
    taxonomy, detail = classify_failure_taxonomy(success=True, failure_reason=FailureReason.TIMEOUT)
    assert taxonomy is FailureTaxonomy.SUCCESS
    assert detail is None


def _sample_result(*, success: int = 0) -> BenchmarkRunResult:
    return BenchmarkRunResult(
        benchmark_version=BENCHMARK_VERSION,
        benchmark_track=BenchmarkTrack.CORE,
        task_id='t1',
        task_version='1.0',
        benchmark_category=BenchmarkCategory.CONFIG_TOOLING_REPAIR,
        task_family=TaskFamily.BUGFIX,
        task_difficulty=TaskDifficulty.EASY,
        task_language='python',
        task_source_type=TaskSource.CURATED,
        task_tags=[],
        task_type='config_tooling_repair',
        benchmark_bucket='baseline',
        runtime_stressors=[],
        expected_files=['src/app.py'],
        task_checksum='abc',
        agent_name='villani',
        adapter_name='villani',
        adapter_version='1',
        adapter_capability='native',
        fairness_classification=FairnessClassification.EXACT_COMPARABLE,
        fairness_notes='ok',
        telemetry_capability='full',
        model_name='m',
        success=success,
        pass_rate=float(success),
        failed=1 - success,
        timed_out=0,
        visible_pass=bool(success),
        hidden_pass=bool(success),
        runtime_seconds=2.0,
        wall_clock_seconds=2.0,
        timeout=False,
        failure_reason=None if success else FailureReason.VISIBLE_VERIFICATION_FAILED,
        error='SyntaxError: invalid syntax' if not success else None,
        touched_file_paths=['src/app.py'] if success else [],
        meaningful_touched_paths=['src/app.py'] if success else [],
        files_touched=1 if success else 0,
        lines_added=2,
        lines_deleted=1,
        num_shell_commands=0 if not success else 2,
        retry_count=1,
        verifications_run=['pytest -q'],
        verification_relevant=True,
        telemetry_quality=TelemetryQuality.EXACT,
        repeat_index=0,
    )


def test_result_serialization_and_reporting_include_failure_taxonomy(tmp_path: Path) -> None:
    row = _sample_result()
    out_dir = tmp_path / 'report'

    results_path = write_results([row], out_dir)
    write_markdown_report([row], out_dir / 'report.md')

    serialized = json.loads(results_path.read_text(encoding='utf-8').splitlines()[0])
    aggregates = json.loads((out_dir / 'aggregates.json').read_text(encoding='utf-8'))
    markdown = (out_dir / 'report.md').read_text(encoding='utf-8')
    summary = render_summary_table([row])
    stats = diagnostics([row])
    summary_json = json.loads((out_dir / 'summary.json').read_text(encoding='utf-8'))

    assert serialized['failure_taxonomy'] == 'syntax_breakage'
    assert serialized['failure_taxonomy_detail'] == 'syntax error detected in execution output'
    assert aggregates['overall']['failure_taxonomy_counts']['syntax_breakage'] == 1
    assert summary_json['by_benchmark_category']['config_tooling_repair']['total'] == 1
    assert summary_json['by_failure_mode_category']['verification_failure']['total'] == 1
    assert summary_json['by_failure_taxonomy']['syntax_breakage']['total'] == 1
    assert 'failure_taxonomy' in (out_dir / 'results.csv').read_text(encoding='utf-8')
    assert 'Failure taxonomy histogram' in markdown
    assert 'syntax_breakage' in summary
    assert stats['failure_taxonomy_histogram']['syntax_breakage'] == 1


def test_failure_taxonomy_ignores_runtime_artifacts_when_classifying_wrong_file_signals() -> None:
    taxonomy, detail = classify_failure_taxonomy(
        success=False,
        failure_reason=FailureReason.VISIBLE_VERIFICATION_FAILED,
        meaningful_touched_paths=[],
        meaningful_expected_paths=[],
        meaningful_unexpected_paths=[],
        touched_file_paths=['.villani_code/transcripts/last.json', '__pycache__/app.pyc'],
        expected_files=['src/app.py'],
        touched_unexpected_files=False,
        unrelated_file_touch=False,
    )

    assert taxonomy is FailureTaxonomy.UNKNOWN_FAILURE
    assert detail is None


def test_rebuild_path_populates_failure_taxonomy(tmp_path: Path) -> None:
    suite = tmp_path / 'benchmark_tasks' / 'villani_bench_v1' / 't1'
    (suite / 'repo').mkdir(parents=True)
    (suite / 'prompt.txt').write_text('Fix the bug', encoding='utf-8')
    (suite / 'task.yaml').write_text(
        '\n'.join(
            [
                'id: t1',
                'benchmark_track: core',
                'family: bugfix',
                'difficulty: easy',
                'language: python',
                'max_minutes: 5',
                'max_files_touched: 2',
                'visible_verification:',
                '  - pytest -q',
                'hidden_verification:',
                '  - pytest -q tests/test_hidden.py',
                'success_policy:',
                '  require_visible_pass: true',
                '  require_hidden_pass: true',
                '  fail_on_timeout: true',
                '  fail_on_repo_dirty_outside_allowlist: true',
                'allowlist_paths:',
                '  - src/',
            ]
        ),
        encoding='utf-8',
    )
    (suite / 'metadata.json').write_text(
        json.dumps({'benchmark_track': 'core', 'benchmark_bucket': 'baseline', 'expected_files': ['src/app.py'], 'task_type': 'unit'}),
        encoding='utf-8',
    )

    out = tmp_path / 'artifacts' / 'benchmark' / 'run'
    (out / 'agent_debug' / 't1__r0').mkdir(parents=True)
    (out / 'manifest_t1_0_111.json').write_text(
        json.dumps(
            {
                'benchmark_version': BENCHMARK_VERSION,
                'task_id': 't1',
                'task_version': '1.0',
                'task_checksum': 'abc',
                'repo_checksum': 'def',
                'visible_check_checksum': 'v',
                'hidden_check_checksum': 'h',
                'adapter_name': 'villani',
                'adapter_version': '1',
                'timeout_seconds': 10,
                'repeat_index': 0,
                'platform': 'linux',
                'python_version': '3.11',
                'agent_name': 'villani',
            }
        ),
        encoding='utf-8',
    )
    (out / 'agent_debug' / 't1__r0' / 'visible_verify_1_meta.json').write_text(
        json.dumps({'stage': 'visible', 'command': 'pytest -q', 'passed': True, 'exit_code': 0}),
        encoding='utf-8',
    )

    rebuild_results_from_directory(out, task_suite_roots=[suite.parent])
    rows = [BenchmarkRunResult.model_validate_json(line) for line in (out / 'results.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]

    assert rows[0].failure_taxonomy is FailureTaxonomy.PARTIAL_FIX_MISSED_ACCEPTANCE
    assert rows[0].failure_taxonomy_detail == 'hidden verification failed after visible verification passed'
