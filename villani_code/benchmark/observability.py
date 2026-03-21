from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObservabilityMetrics:
    """Derived benchmark observability metrics computed from cleaned benchmark evidence.

    `expected_files_found` intentionally means "expected files with meaningful interaction
    evidence" rather than on-disk existence. Interaction evidence includes meaningful reads
    and meaningful touches so the metric stays consistent with `expected_files_touched_count`.
    """

    expected_files_found: int
    expected_files_total: int
    expected_files_touched_count: int
    first_pass_success: bool
    recovered_after_failed_attempt: bool
    recovery_attempted: bool
    recovery_success: bool | None
    self_corrected_after_failed_verify: bool


def derive_observability_metrics(
    *,
    success: bool,
    expected_files: list[str],
    expected_files_read: set[str],
    expected_files_touched: set[str],
    verification_failed_then_recovered: bool,
    retries_after_failure: int | None,
) -> ObservabilityMetrics:
    meaningful_expected_interactions = set(expected_files_read) | set(expected_files_touched)
    expected_total = len(expected_files)
    recovery_attempted = verification_failed_then_recovered or (retries_after_failure or 0) > 0
    recovered = bool(success and recovery_attempted)
    return ObservabilityMetrics(
        expected_files_found=len(meaningful_expected_interactions),
        expected_files_total=expected_total,
        expected_files_touched_count=len(expected_files_touched),
        first_pass_success=bool(success and not recovery_attempted),
        recovered_after_failed_attempt=recovered,
        recovery_attempted=recovery_attempted,
        recovery_success=(recovered if recovery_attempted else None),
        self_corrected_after_failed_verify=bool(success and verification_failed_then_recovered),
    )
