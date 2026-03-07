from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RUN_MODE_SAME_BACKEND = "same-backend"
RUN_MODE_NATIVE_CLI = "native-cli"
RUN_MODE_MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    supports_explicit_base_url: bool
    supports_explicit_model: bool
    supports_noninteractive: bool
    supports_unattended: bool
    default_fairness_classification: str
    controllability_note: str


def classify_run_mode(
    *,
    agents: list[str],
    base_url: str | None,
    model: str | None,
    capabilities: dict[str, AdapterCapabilities],
) -> tuple[str, str | None]:
    if len(agents) <= 1:
        return RUN_MODE_NATIVE_CLI, None
    if not base_url or not model:
        return RUN_MODE_MIXED, "Fairness warning: mixed-mode comparison (base_url/model not fully pinned)."

    all_explicit = all(
        capabilities.get(agent)
        and capabilities[agent].supports_explicit_base_url
        and capabilities[agent].supports_explicit_model
        for agent in agents
    )
    if all_explicit:
        return RUN_MODE_SAME_BACKEND, None
    return RUN_MODE_MIXED, "Fairness warning: mixed-mode comparison (not all adapters support explicit backend+model)."


def capability_table_payload(agents: list[str], capabilities: dict[str, AdapterCapabilities]) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for agent in agents:
        cap = capabilities[agent]
        table.append(
            {
                "agent": agent,
                "supports_explicit_base_url": cap.supports_explicit_base_url,
                "supports_explicit_model": cap.supports_explicit_model,
                "supports_noninteractive": cap.supports_noninteractive,
                "supports_unattended": cap.supports_unattended,
                "fairness_classification": cap.default_fairness_classification,
                "controllability": cap.controllability_note,
            }
        )
    return table
