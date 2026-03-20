from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class RunnerBenchmarkConfig(Protocol):
    enabled: bool
    task_id: str
    allowlist_paths: list[str]
    forbidden_paths: list[str]
    expected_files: list[str]
    allowed_support_files: list[str]
    allowed_support_globs: list[str]
    max_files_touched: int
    require_patch_artifact: bool
    visible_verification: list[str]
    hidden_verification: list[str]

    def normalized_path(self, raw_path: str) -> str: ...

    def in_allowlist(self, raw_path: str) -> bool: ...

    def in_forbidden(self, raw_path: str) -> bool: ...

    def is_expected_or_support(self, raw_path: str) -> bool: ...


@dataclass(slots=True)
class DisabledBenchmarkConfig:
    enabled: bool = False
    task_id: str = ""
    allowlist_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    allowed_support_files: list[str] = field(default_factory=list)
    allowed_support_globs: list[str] = field(default_factory=list)
    max_files_touched: int = 1
    require_patch_artifact: bool = True
    visible_verification: list[str] = field(default_factory=list)
    hidden_verification: list[str] = field(default_factory=list)

    def normalized_path(self, raw_path: str) -> str:
        return str(raw_path or "").replace("\\", "/").lstrip("./")

    def in_allowlist(self, raw_path: str) -> bool:
        return False

    def in_forbidden(self, raw_path: str) -> bool:
        return False

    def is_expected_or_support(self, raw_path: str) -> bool:
        return False
