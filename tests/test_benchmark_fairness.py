from __future__ import annotations

from villani_code.benchmark.fairness import (
    AdapterCapabilities,
    classify_run_mode,
)


def _cap(base_url: bool, model: bool) -> AdapterCapabilities:
    return AdapterCapabilities(
        supports_explicit_base_url=base_url,
        supports_explicit_model=model,
        supports_noninteractive=True,
        supports_unattended=True,
        default_fairness_classification="mixed",
        controllability_note="x",
    )


def test_same_backend_mode_requires_all_adapters_explicit() -> None:
    mode, warning = classify_run_mode(
        agents=["villani", "other"],
        base_url="http://localhost:8000",
        model="demo",
        capabilities={"villani": _cap(True, True), "other": _cap(True, True)},
    )
    assert mode == "same-backend"
    assert warning is None


def test_mixed_mode_warning_when_any_adapter_cannot_pin_backend_model() -> None:
    mode, warning = classify_run_mode(
        agents=["villani", "claude-code"],
        base_url="http://localhost:8000",
        model="demo",
        capabilities={"villani": _cap(True, True), "claude-code": _cap(False, False)},
    )
    assert mode == "mixed"
    assert warning is not None
    assert "Fairness warning" in warning
