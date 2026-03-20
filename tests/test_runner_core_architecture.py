from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_MODULES = (
    "villani_code/state.py",
    "villani_code/state_runtime.py",
    "villani_code/state_tooling.py",
)
BENCHMARK_IMPORT_ALLOWLIST: frozenset[str] = frozenset()
# The boundary is currently clean; keep this empty unless an existing dependency must be grandfathered.

EXPECTED_CORE_PUBLIC_SURFACE = {
    "villani_code/state.py": {"Runner"},
    "villani_code/state_runtime.py": {
        "parse_failure_signal",
        "run_pre_edit_failure_localization",
        "classify_diagnosis_target_confidence",
        "parse_pre_edit_diagnosis",
        "run_pre_edit_diagnosis",
        "inject_diagnosis_hint",
        "prepare_messages_for_model",
        "validate_anthropic_tool_sequence",
        "inject_retrieval_briefing",
        "init_small_model_support",
        "small_model_tool_guard",
        "tighten_tool_input",
        "truncate_tool_result",
        "git_changed_files",
        "run_post_edit_verification",
        "run_verification",
        "emit_policy_event",
        "capture_edit_proposal",
        "is_no_progress_response",
        "save_session_snapshot",
        "render_stream_event",
        "ensure_project_memory_and_plan",
        "run_post_execution_validation",
    },
    "villani_code/state_tooling.py": {"execute_tool_with_policy"},
}

TUI_RUNNER_ENTRYPOINTS = {"run", "plan", "run_with_plan", "run_villani_mode"}
FORBIDDEN_AUTONOMOUS_RUNTIME_CALLS = {
    "create_message",
    "execute_tool",
    "execute_tool_with_policy",
    "run_with_plan",
    "plan",
    "run_villani_mode",
}
FORBIDDEN_BENCHMARK_IMPORT_PREFIXES = (
    "villani_code.state_runtime",
    "villani_code.state_tooling",
)
FORBIDDEN_CORE_BENCHMARK_IMPORT_PREFIXES = (
    "villani_code.benchmark.reporting",
    "villani_code.benchmark.agent_runner",
    "villani_code.benchmark.agents",
)


def _module_path(*parts: str) -> Path:
    return REPO_ROOT.joinpath(*parts)


def _iter_python_files(relative_root: str) -> list[Path]:
    return sorted(_module_path(relative_root).rglob("*.py"))


def _parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_imports(path: Path) -> set[str]:
    tree = _parse_module(path)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _call_matches(node: ast.Call, *, owner: str, attr: str) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == attr
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == owner
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "self"
    )


def _top_level_public_defs(path: Path) -> set[str]:
    tree = _parse_module(path)
    public: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
            public.add(node.name)
    return public


def test_benchmark_package_does_not_import_runner_runtime_or_tool_policy_directly() -> None:
    violations: list[str] = []
    for path in _iter_python_files("villani_code/benchmark"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in BENCHMARK_IMPORT_ALLOWLIST:
            continue
        imports = _module_imports(path)
        forbidden = sorted(
            name for name in imports if name.startswith(FORBIDDEN_BENCHMARK_IMPORT_PREFIXES)
        )
        if forbidden:
            violations.append(f"{rel}: {', '.join(forbidden)}")
    assert not violations, "Benchmark package must stay layered above the runner core:\n" + "\n".join(violations)


def test_tui_modules_route_runner_execution_through_controller() -> None:
    violations: list[str] = []
    for path in _iter_python_files("villani_code/tui"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = _parse_module(path)
        if rel == "villani_code/tui/controller.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(
                _call_matches(node, owner="runner", attr=entrypoint) for entrypoint in TUI_RUNNER_ENTRYPOINTS
            ):
                violations.append(f"{rel}:{node.lineno}")
    assert not violations, "TUI execution should flow through RunnerController:\n" + "\n".join(violations)

    app_imports = _module_imports(_module_path("villani_code", "tui", "app.py"))
    assert "villani_code.tui.controller" in app_imports


def test_autonomous_mode_consumes_runner_without_defining_a_second_generic_tool_loop() -> None:
    path = _module_path("villani_code", "autonomous.py")
    tree = _parse_module(path)
    direct_runtime_calls: list[str] = []
    runner_run_calls = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Attribute):
                if node.func.value.attr == "runner" and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == "self":
                    if node.func.attr == "run":
                        runner_run_calls += 1
                    elif node.func.attr in FORBIDDEN_AUTONOMOUS_RUNTIME_CALLS:
                        direct_runtime_calls.append(f"self.runner.{node.func.attr} @ line {node.lineno}")
            elif isinstance(node.func.value, ast.Name) and node.func.attr in {
                "create_message",
                "execute_tool",
                "execute_tool_with_policy",
            }:
                direct_runtime_calls.append(f"{ast.unparse(node.func)} @ line {node.lineno}")

    imports = _module_imports(path)
    forbidden_imports = sorted(
        name
        for name in imports
        if name in {"villani_code.tools", "villani_code.state_tooling", "villani_code.state_runtime", "villani_code.llm_client"}
    )

    assert runner_run_calls >= 1, "Autonomous mode should delegate work to Runner.run()."
    assert not direct_runtime_calls, "Autonomous mode must not grow its own generic execution loop:\n" + "\n".join(direct_runtime_calls)
    assert not forbidden_imports, "Autonomous mode must not import low-level runtime/tool executors directly."


FORBIDDEN_STATE_TOOLING_HELPERS = {
    "_benchmark_mutation_targets",
    "_benchmark_post_write_python_validation",
    "_validate_benchmark_mutation",
    "_parse_benchmark_denial_message",
    "_benchmark_denial_feedback",
    "benchmark_mutation_targets",
    "benchmark_post_write_python_validation",
    "validate_benchmark_mutation",
    "parse_benchmark_denial_message",
    "benchmark_denial_feedback",
}


def test_state_tooling_does_not_define_benchmark_specific_policy_helpers() -> None:
    path = _module_path("villani_code", "state_tooling.py")
    tree = _parse_module(path)
    helpers = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FORBIDDEN_STATE_TOOLING_HELPERS
    }
    assert not helpers, (
        "state_tooling.py should call benchmark tool policy helpers from villani_code.benchmark.tool_policy, not define them: "
        + ", ".join(sorted(helpers))
    )


def test_runner_core_does_not_import_benchmark_reporting_or_agent_code() -> None:
    violations: list[str] = []
    for rel in CORE_MODULES:
        path = _module_path(*rel.split("/"))
        imports = _module_imports(path)
        forbidden = sorted(
            name for name in imports if name.startswith(FORBIDDEN_CORE_BENCHMARK_IMPORT_PREFIXES)
        )
        if forbidden:
            violations.append(f"{rel}: {', '.join(forbidden)}")
    assert not violations, "Runner core must not depend on benchmark reporting or agent layers:\n" + "\n".join(violations)


def test_runner_core_public_surface_stays_small_and_intentional() -> None:
    actual = {
        rel: _top_level_public_defs(_module_path(*rel.split("/")))
        for rel in EXPECTED_CORE_PUBLIC_SURFACE
    }
    assert actual == EXPECTED_CORE_PUBLIC_SURFACE
