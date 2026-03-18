from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.indexing import FileInfo, RepoIndex


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z0-9_\.]+)", re.MULTILINE)
_PATH_RE = re.compile(r"([A-Za-z0-9_./\\-]+\.(?:py|toml|ini|cfg|json|yaml|yml|mk))")
_CONFIG_NAMES = {
    "pyproject.toml",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "Makefile",
    "makefile",
    "tests/conftest.py",
}


@dataclass(slots=True)
class LocalizationCandidate:
    path: str
    authority_tier: int
    score: float
    summary: str
    symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BenchmarkLocalizationPack:
    repo_map_summary: str
    likely_test_roots: list[str]
    likely_source_roots: list[str]
    expected_task_files: list[str]
    top_candidate_files: list[LocalizationCandidate]
    related_symbols: list[str] = field(default_factory=list)
    related_imports: list[str] = field(default_factory=list)

    def prompt_text(self) -> str:
        lines = [
            "Compact benchmark localization pack:",
            f"- Likely source roots: {', '.join(self.likely_source_roots) or 'none'}",
            f"- Likely test roots: {', '.join(self.likely_test_roots) or 'none'}",
            f"- Expected task files: {', '.join(self.expected_task_files) or 'none'}",
            "- Top candidate files:",
        ]
        for candidate in self.top_candidate_files[:6]:
            reason_text = "; ".join(candidate.reasons[:3]) or "task relevance"
            symbol_text = ", ".join(candidate.symbols[:4]) or "-"
            import_text = ", ".join(candidate.imports[:3]) or "-"
            lines.append(
                f"  - [tier {candidate.authority_tier}] {candidate.path} :: {candidate.summary} "
                f"(why: {reason_text}; symbols: {symbol_text}; imports: {import_text})"
            )
        if self.related_symbols:
            lines.append(f"- Related symbols: {', '.join(self.related_symbols[:10])}")
        if self.related_imports:
            lines.append(f"- Related imports: {', '.join(self.related_imports[:10])}")
        lines.append("- Priority order: expected files > verifier/traceback files > adjacent implementation > paired tests > config/build only with explicit evidence.")
        return "\n".join(lines)

    def editable_candidates(self) -> set[str]:
        return {candidate.path for candidate in self.top_candidate_files} | set(self.expected_task_files)

    def authority_for_path(self, path: str) -> LocalizationCandidate | None:
        normalized = _normalize_path(path)
        for candidate in self.top_candidate_files:
            if candidate.path == normalized:
                return candidate
        return None


@dataclass(slots=True)
class VerificationFailureEvidence:
    classification: str
    repair_decision: str
    summary: str
    traceback_files: list[str] = field(default_factory=list)
    assertion_targets: list[str] = field(default_factory=list)
    relevant_symbols: list[str] = field(default_factory=list)
    environment_failure: bool = False
    targeted_candidates: list[str] = field(default_factory=list)
    raw_excerpt: str = ""


def _normalize_path(value: str) -> str:
    return str(value or "").replace("\\", "/").lstrip("./")


def _tokenize(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(text or "")]


def _file_summary(file_info: FileInfo) -> str:
    for line in file_info.snippet.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "import ", "from ")):
            return stripped[:120]
    return file_info.lang or "file"


def _extract_imports(snippet: str) -> list[str]:
    imports: list[str] = []
    for match in _IMPORT_RE.finditer(snippet or ""):
        name = match.group(1).strip()
        if name and name not in imports:
            imports.append(name)
    return imports[:6]


def _adjacent_paths(path: str) -> list[str]:
    normalized = _normalize_path(path)
    p = Path(normalized)
    stem = p.stem.removeprefix("test_")
    candidates = []
    parent = p.parent.as_posix() if str(p.parent) != "." else ""
    if stem:
        if parent:
            candidates.append(f"{parent}/{stem}.py")
            candidates.append(f"{parent}/test_{stem}.py")
        candidates.append(f"tests/test_{stem}.py")
        candidates.append(f"src/{stem}.py")
    return [_normalize_path(item) for item in candidates if item]


def _infer_authority_tier(
    path: str,
    expected_files: set[str],
    traceback_files: set[str],
    assertion_targets: set[str],
    explicit_config_evidence: bool,
) -> int:
    normalized = _normalize_path(path)
    if normalized in expected_files:
        return 1
    if normalized in traceback_files or normalized in assertion_targets:
        return 2
    if Path(normalized).name in _CONFIG_NAMES or normalized.endswith((".toml", ".ini", ".cfg")):
        return 5 if explicit_config_evidence else 6
    if normalized.startswith("tests/"):
        return 4
    return 3


def _lexical_score(path: str, instruction: str, file_info: FileInfo, failure: VerificationFailureEvidence | None) -> float:
    query_tokens = set(_tokenize(instruction))
    if failure:
        query_tokens.update(_tokenize(failure.summary))
        query_tokens.update(token.casefold() for token in failure.relevant_symbols)
    path_tokens = set(_tokenize(path.replace("/", " ")))
    symbol_tokens = set(token.casefold() for token in file_info.symbols)
    import_tokens = set(token.casefold() for token in _extract_imports(file_info.snippet))
    overlap = query_tokens & (path_tokens | symbol_tokens | import_tokens)
    return float(len(overlap))


def classify_verification_failure(
    failure_summary: str,
    compact_output: str = "",
    relevant_paths: list[str] | None = None,
) -> VerificationFailureEvidence:
    text = "\n".join(part for part in [failure_summary, compact_output] if part)
    lowered = text.casefold()
    paths = [_normalize_path(path) for path in (relevant_paths or [])]
    paths.extend(_normalize_path(match.group(1)) for match in _PATH_RE.finditer(text))
    traceback_files = list(dict.fromkeys(path for path in paths if path))
    assertion_targets = [path for path in traceback_files if path.endswith(".py")]
    symbols = [match.group(0) for match in _TOKEN_RE.finditer(text) if match.group(0)[:1].isalpha()][:10]
    classification = "assertion_mismatch"
    repair_decision = "same_file_correction"
    environment = False
    summary = failure_summary.strip() or compact_output.strip()[:200] or "verification failed"

    if "no module named" in lowered or "modulenotfounderror" in lowered or "importerror" in lowered:
        classification = "import_module_error"
        repair_decision = "missing_adjacent_change"
        if "src/" in lowered or "from src" in lowered or "pythonpath" in lowered:
            environment = True
            classification = "src_layout_import_error"
            repair_decision = "environment_harness_issue"
    elif "make: not found" in lowered or "'make' is not recognized" in lowered:
        classification = "missing_make"
        repair_decision = "environment_harness_issue"
        environment = True
    elif "assert" in lowered or "assertionerror" in lowered:
        classification = "assertion_mismatch"
        repair_decision = "same_file_correction"
    elif "header" in lowered and any(token in lowered for token in ["missing", "propagation", "authorization"]):
        classification = "http_header_propagation_mismatch"
        repair_decision = "missing_adjacent_change"
    elif "config" in lowered or "environment variable" in lowered or "precedence" in lowered:
        classification = "config_precedence_env_handling"
        repair_decision = "same_file_correction"
    elif "path" in lowered and any(token in lowered for token in ["windows", "separator", "normalize"]):
        classification = "path_normalization_platform_issue"
        repair_decision = "same_file_correction"
    elif "bash: not found" in lowered or "powershell" in lowered or "shell" in lowered and "not found" in lowered:
        classification = "shell_incompatibility"
        repair_decision = "environment_harness_issue"
        environment = True
    elif "poetry: not found" in lowered or "uv: not found" in lowered or "pytest: not found" in lowered:
        classification = "missing_command_runner_dependency"
        repair_decision = "environment_harness_issue"
        environment = True
    elif "edit_authority_denied" in lowered or "forbidden" in lowered and "path=" in lowered:
        classification = "forbidden_target_drift"
        repair_decision = "forbidden_target_drift"
    elif "no patch" in lowered or "no_patch" in lowered:
        classification = "no_patch_produced"
        repair_decision = "no_patch_produced"
    elif not traceback_files and ("failed" in lowered or "mismatch" in lowered):
        classification = "wrong_localization"
        repair_decision = "wrong_file_localized"

    targeted = traceback_files[:4]
    return VerificationFailureEvidence(
        classification=classification,
        repair_decision=repair_decision,
        summary=summary[:220],
        traceback_files=traceback_files[:6],
        assertion_targets=assertion_targets[:6],
        relevant_symbols=symbols[:10],
        environment_failure=environment,
        targeted_candidates=targeted,
        raw_excerpt=text[:1200],
    )


def build_benchmark_localization_pack(
    repo: Path,
    instruction: str,
    repo_map: dict[str, Any],
    benchmark_config: BenchmarkRuntimeConfig,
    *,
    index: RepoIndex | None = None,
    failure: VerificationFailureEvidence | None = None,
) -> BenchmarkLocalizationPack:
    if index is None:
        index = RepoIndex.build(repo)
    files = list(index.iter_files())
    by_path = {file_info.path: file_info for file_info in files}
    expected_files = {_normalize_path(path) for path in benchmark_config.expected_files}
    traceback_files = set(failure.traceback_files if failure else [])
    assertion_targets = set(failure.assertion_targets if failure else [])
    explicit_config_evidence = bool(
        failure
        and (
            any(Path(path).name in _CONFIG_NAMES for path in traceback_files | assertion_targets)
            or failure.classification in {"config_precedence_env_handling", "missing_make"}
        )
    )
    related_paths: set[str] = set(expected_files | traceback_files | assertion_targets)
    for item in list(related_paths):
        related_paths.update(_adjacent_paths(item))

    candidates: list[LocalizationCandidate] = []
    for file_info in files:
        path = _normalize_path(file_info.path)
        tier = _infer_authority_tier(
            path,
            expected_files=expected_files,
            traceback_files=traceback_files,
            assertion_targets=assertion_targets,
            explicit_config_evidence=explicit_config_evidence,
        )
        lexical = _lexical_score(path, instruction, file_info, failure)
        score = lexical
        reasons: list[str] = []
        if path in expected_files:
            score += 1000.0
            reasons.append("explicit expected task file")
        if path in traceback_files:
            score += 900.0
            reasons.append("named in traceback or failing output")
        if path in assertion_targets:
            score += 850.0
            reasons.append("referenced by failing assertion")
        if path in related_paths:
            score += 450.0
            reasons.append("adjacent implementation/test candidate")
        if lexical:
            reasons.append(f"lexical overlap score={int(lexical)}")
        if path.startswith("tests/") and path not in expected_files and path not in traceback_files:
            score -= 120.0
            reasons.append("paired test candidate only")
        if (Path(path).name in _CONFIG_NAMES or path.endswith((".toml", ".ini", ".cfg"))) and not explicit_config_evidence:
            score -= 500.0
            reasons.append("config/build deprioritized without verifier evidence")
        if score <= 0:
            continue
        candidates.append(
            LocalizationCandidate(
                path=path,
                authority_tier=tier,
                score=score,
                summary=_file_summary(file_info),
                symbols=file_info.symbols[:6],
                imports=_extract_imports(file_info.snippet),
                reasons=reasons[:4],
            )
        )

    candidates.sort(key=lambda item: (item.authority_tier, -item.score, item.path))
    top_candidates = candidates[:6]
    related_symbols: list[str] = []
    related_imports: list[str] = []
    for candidate in top_candidates:
        for symbol in candidate.symbols:
            if symbol not in related_symbols:
                related_symbols.append(symbol)
        for import_name in candidate.imports:
            if import_name not in related_imports:
                related_imports.append(import_name)

    repo_summary = str(repo_map.get("summary", {}).get("shape", "")) or str(repo_map.get("repo_shape", ""))
    repo_tree = []
    for key in ("package_roots", "source_roots", "test_roots", "manifests", "config_files", "likely_entrypoints"):
        values = [str(value) for value in repo_map.get(key, [])[:4]]
        if values:
            repo_tree.append(f"{key}={', '.join(values)}")
    if repo_summary:
        repo_tree.insert(0, f"shape={repo_summary}")

    return BenchmarkLocalizationPack(
        repo_map_summary="; ".join(repo_tree)[:600],
        likely_test_roots=[str(value) for value in repo_map.get("test_roots", [])[:6]],
        likely_source_roots=[str(value) for value in repo_map.get("source_roots", [])[:6]],
        expected_task_files=sorted(expected_files)[:8],
        top_candidate_files=top_candidates,
        related_symbols=related_symbols[:10],
        related_imports=related_imports[:10],
    )
