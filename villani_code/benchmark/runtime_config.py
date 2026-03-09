from __future__ import annotations

from fnmatch import fnmatch

from pydantic import BaseModel, Field

from villani_code.benchmark.policy import normalize_path


def _scope_match(path: str, scope: str) -> bool:
    scoped = normalize_path(scope)
    if scope.endswith("/"):
        return path == scoped or path.startswith(f"{scoped}/")
    return path == scoped


class BenchmarkRuntimeConfig(BaseModel):
    enabled: bool = False
    task_id: str = ""
    allowlist_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    expected_files: list[str] = Field(default_factory=list)
    allowed_support_files: list[str] = Field(default_factory=list)
    allowed_support_globs: list[str] = Field(default_factory=list)
    max_files_touched: int = 1
    require_patch_artifact: bool = True
    visible_verification: list[str] = Field(default_factory=list)
    hidden_verification: list[str] = Field(default_factory=list)

    def normalized_path(self, raw_path: str) -> str:
        return normalize_path(raw_path)

    def in_allowlist(self, raw_path: str) -> bool:
        path = self.normalized_path(raw_path)
        return any(_scope_match(path, scope) for scope in self.allowlist_paths)

    def in_forbidden(self, raw_path: str) -> bool:
        path = self.normalized_path(raw_path)
        return any(_scope_match(path, scope) for scope in self.forbidden_paths)

    def is_expected_or_support(self, raw_path: str) -> bool:
        path = self.normalized_path(raw_path)
        expected = {normalize_path(item) for item in self.expected_files}
        if path in expected:
            return True
        support = {normalize_path(item) for item in self.allowed_support_files}
        if path in support:
            return True
        return any(fnmatch(path.casefold(), normalize_path(pattern).casefold()) for pattern in self.allowed_support_globs)


def benchmark_runtime_config_from_task(task: object) -> BenchmarkRuntimeConfig:
    metadata = getattr(task, "metadata")
    expected_artifacts = list(getattr(task, "expected_artifacts", []))
    return BenchmarkRuntimeConfig(
        enabled=True,
        task_id=str(getattr(task, "id")),
        allowlist_paths=list(getattr(task, "allowlist_paths", [])) or list(getattr(task, "allowed_paths", [])),
        forbidden_paths=list(getattr(task, "forbidden_paths")),
        expected_files=list(getattr(metadata, "expected_files", [])),
        allowed_support_files=list(getattr(metadata, "allowed_support_files", [])),
        allowed_support_globs=list(getattr(metadata, "allowed_support_globs", [])),
        max_files_touched=int(getattr(task, "max_files_touched", 1)),
        require_patch_artifact="patch" in expected_artifacts,
        visible_verification=list(getattr(task, "visible_verification", [])),
        hidden_verification=list(getattr(task, "hidden_verifier") or getattr(task, "hidden_verification", [])),
    )
