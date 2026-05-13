from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

import typer
from rich.console import Console
from pydantic import BaseModel, ValidationError

from villani_code.interrupts import InterruptController
from villani_code.optional_tui import OptionalTUIDependencyError, TUI_INSTALL_HINT
from villani_code.anthropic_client import AnthropicClient
from villani_code.openai_client import OpenAIClient
from villani_code.runtime_safety import ensure_runtime_dependencies_not_shadowed
from villani_code.state import Runner
from villani_code.context_governance import ContextGovernanceManager
from villani_code.cli_subcommands import register_benchmark_commands, register_mcp_commands, register_plugin_commands
from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.debug_bundle import create_debug_bundle
from villani_code.debug_mode import DebugMode, build_debug_config
from villani_code.trace_summary import write_summary_from_events, write_tool_calls_from_events
from villani_code.orchestrator import OrchestratorConfig, run_orchestrator
from villani_code.mission_state import set_current_mission_id

app = typer.Typer(help="Villani: constrained-inference coding agent with visible context governance")
mcp_app = typer.Typer(help="Manage MCP servers")
plugin_app = typer.Typer(help="Manage local plugins")
benchmark_app = typer.Typer(help="Objective repository benchmark tasks")
trace_app = typer.Typer(help="Trace/debug artifact utilities")
app.add_typer(mcp_app, name="mcp")
app.add_typer(plugin_app, name="plugin")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(trace_app, name="trace")
console = Console()

BaseUrlOption = Annotated[str, typer.Option(..., "--base-url", help="Base URL for compatible messages API server")]
ModelOption = Annotated[str, typer.Option(..., "--model", help="Model name")]
RepoOption = Annotated[Path, typer.Option("--repo", help="Repository path")]
MaxTokensOption = Annotated[int, typer.Option("--max-tokens")]
StreamOption = Annotated[bool, typer.Option("--stream/--no-stream")]
ThinkingOption = Annotated[Optional[str], typer.Option("--thinking")]
UnsafeOption = Annotated[bool, typer.Option("--unsafe")]
VerboseOption = Annotated[bool, typer.Option("--verbose")]
ExtraJsonOption = Annotated[Optional[str], typer.Option("--extra-json")]
RedactOption = Annotated[bool, typer.Option("--redact")]
SkipPermissionsOption = Annotated[bool, typer.Option("--dangerously-skip-permissions")]
AutoAcceptEditsOption = Annotated[bool, typer.Option("--auto-accept-edits")]
AutoApproveOption = Annotated[bool, typer.Option("--auto-approve", help="Automatically approve all actions without prompting")]
PlanModeOption = Annotated[Literal["off", "auto", "strict"], typer.Option("--plan-mode")]
MaxRepairAttemptsOption = Annotated[int, typer.Option("--max-repair-attempts")]
SmallModelOption = Annotated[bool, typer.Option("--small-model")]
ProviderOption = Annotated[Literal["anthropic", "openai"], typer.Option("--provider")]
ApiKeyOption = Annotated[Optional[str], typer.Option("--api-key")]
BenchmarkRuntimeOption = Annotated[Optional[str], typer.Option("--benchmark-runtime-json", hidden=True)]
DebugOption = Annotated[Optional[str], typer.Option("--debug", flag_value="normal")]
DebugDirOption = Annotated[Optional[Path], typer.Option("--debug-dir")]


def _print_response_text_blocks(result: dict[str, Any] | None) -> None:
    def _print_content(value: Any) -> None:
        if isinstance(value, str):
            console.print(value)
            return
        if not isinstance(value, list):
            return
        for block in value:
            if isinstance(block, str):
                console.print(block)
                continue
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str):
                console.print(text)

    try:
        if not isinstance(result, dict):
            return

        response = result.get("response")
        if isinstance(response, str):
            console.print(response)

        if isinstance(response, dict):
            _print_content(response.get("content"))

        if "content" in result:
            _print_content(result.get("content"))
    except Exception:  # noqa: BLE001
        return


def _extract_response_text(result: dict[str, Any] | None) -> str:
    chunks: list[str] = []
    if not isinstance(result, dict):
        return ""
    response = result.get("response")
    if isinstance(response, str):
        chunks.append(response)
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, str):
                    chunks.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(str(block.get("text", "")))
    direct_content = result.get("content")
    if isinstance(direct_content, list):
        for block in direct_content:
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(str(block.get("text", "")))
    return "\n".join(c for c in chunks if c.strip()).strip()


class _SupervisorArtifact(BaseModel):
    subtasks: list[dict[str, Any]]


class _WorkerArtifact(BaseModel):
    status: str
    summary: str
    files_touched: list[str] = []
    recommended_verification: list[str] = []


def _write_result_artifact(path: Path, role: str, result: dict[str, Any] | None) -> None:
    raw = _extract_response_text(result)
    parsed: dict[str, Any] = {}
    try:
        parsed_candidate = json.loads(raw) if raw else {}
        if isinstance(parsed_candidate, dict):
            parsed = parsed_candidate
    except json.JSONDecodeError:
        parsed = {}
    if role == "supervisor":
        try:
            payload = _SupervisorArtifact.model_validate(parsed).model_dump()
        except ValidationError:
            payload = {"subtasks": []}
    elif role == "worker":
        try:
            candidate = _WorkerArtifact.model_validate(parsed).model_dump()
            if candidate["status"] in {"success", "blocked_environment", "blocked_scope", "failed"}:
                payload = candidate
            else:
                payload = {"status": "failed", "summary": "invalid worker json", "files_touched": [], "recommended_verification": []}
        except ValidationError:
            payload = {"status": "failed", "summary": "invalid worker json", "files_touched": [], "recommended_verification": []}
    else:
        payload = parsed if parsed else {"result": raw}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

def _load_settings_manager() -> Any | None:
    try:
        from villani_code.tui.components.settings import SettingsManager
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            return None
        raise
    return SettingsManager


def _load_interactive_shell() -> tuple[Any, type[Exception]]:
    from villani_code.interactive import InteractiveShell

    return InteractiveShell, OptionalTUIDependencyError


def _resolve_villani_flag(repo: Path, cli_value: bool | None) -> bool:
    if cli_value is not None:
        return cli_value
    settings_manager = _load_settings_manager()
    if settings_manager is None:
        return False
    settings = settings_manager(repo.resolve()).load()
    return bool(getattr(settings, "villani_mode", False))


def _build_runner(base_url: str, model: str, repo: Path, max_tokens: int, stream: bool, thinking: Optional[str], unsafe: bool, verbose: bool, extra_json: Optional[str], redact: bool, dangerously_skip_permissions: bool, auto_accept_edits: bool, auto_approve: bool, plan_mode: Literal["off", "auto", "strict"], max_repair_attempts: int, small_model: bool, provider: Literal["anthropic", "openai"], api_key: Optional[str], villani_mode: bool = False, villani_objective: str | None = None, benchmark_runtime_json: str | None = None, debug_mode: DebugMode = DebugMode.OFF, debug_dir: Optional[Path] = None) -> Runner:
    resolved_repo = repo.resolve()
    try:
        ensure_runtime_dependencies_not_shadowed(resolved_repo)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc

    client: Any
    if provider == "openai":
        resolved_api_key = api_key or os.environ.get("OPENAI_API_KEY")
        client = OpenAIClient(base_url=base_url, api_key=resolved_api_key)
    else:
        _ = api_key or os.environ.get("ANTHROPIC_API_KEY")
        client = AnthropicClient(base_url=base_url)
    thinking_obj = None
    if thinking:
        try:
            thinking_obj = json.loads(thinking)
        except json.JSONDecodeError:
            thinking_obj = thinking
    benchmark_config = BenchmarkRuntimeConfig.model_validate_json(benchmark_runtime_json) if benchmark_runtime_json else None
    debug_config = build_debug_config(debug_mode.value if isinstance(debug_mode, DebugMode) else str(debug_mode), debug_dir=debug_dir)
    return Runner(client=client, repo=resolved_repo, model=model, max_tokens=max_tokens, stream=stream, thinking=thinking_obj, unsafe=unsafe, verbose=verbose, extra_json=extra_json, redact=redact, bypass_permissions=dangerously_skip_permissions, auto_accept_edits=auto_accept_edits, auto_approve=auto_approve, plan_mode=plan_mode, max_repair_attempts=max_repair_attempts, small_model=small_model, villani_mode=villani_mode, villani_objective=villani_objective, benchmark_config=benchmark_config, debug_config=debug_config, provider=provider)


def _run_interactive(base_url: str, model: str, repo: Path, max_tokens: int, small_model: bool, provider: Literal["anthropic", "openai"], api_key: Optional[str], villani_mode: bool = False, villani_objective: str | None = None, auto_approve: bool = False, debug_mode: DebugMode = DebugMode.OFF, debug_dir: Optional[Path] = None) -> None:
    runner = _build_runner(base_url, model, repo, max_tokens, True, None, False, False, None, False, False, False, auto_approve, "auto", 2, small_model, provider, api_key, villani_mode=villani_mode, villani_objective=villani_objective, debug_mode=debug_mode, debug_dir=debug_dir)
    if auto_approve:
        console.print("Auto-approval: ON")
    try:
        shell_cls, dependency_error = _load_interactive_shell()
        shell = shell_cls(runner, repo.resolve(), villani_mode=villani_mode, villani_objective=villani_objective)
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            raise typer.BadParameter(
                TUI_INSTALL_HINT
            ) from exc
        raise
    except dependency_error as exc:
        raise typer.BadParameter(str(exc)) from exc
    interrupts = InterruptController()
    while True:
        try:
            shell.run()
            interrupts.reset_interrupt_state()
            return
        except ModuleNotFoundError as exc:
            if exc.name == "textual":
                raise typer.BadParameter(
                    TUI_INSTALL_HINT
                ) from exc
            raise
        except dependency_error as exc:
            raise typer.BadParameter(str(exc)) from exc
        except KeyboardInterrupt:
            action = interrupts.register_interrupt()
            if action == "exit":
                raise typer.Exit(code=130)
            console.print("Interrupted current session. Press Ctrl+C again to exit Villani Code.")


def _build_inherited_run_args(
    *,
    base_url: str,
    model: str,
    repo: Path,
    max_tokens: int,
    stream: bool,
    thinking: Optional[str],
    unsafe: bool,
    verbose: bool,
    extra_json: Optional[str],
    redact: bool,
    dangerously_skip_permissions: bool,
    auto_accept_edits: bool,
    auto_approve: bool,
    plan_mode: Literal["off", "auto", "strict"],
    max_repair_attempts: int,
    small_model: bool,
    provider: Literal["anthropic", "openai"],
    api_key: Optional[str],
    benchmark_runtime_json: Optional[str],
    debug: Optional[str],
    debug_dir: Optional[Path],
    passthrough_args: list[str],
) -> list[str]:
    args: list[str] = [
        "--base-url", base_url,
        "--model", model,
        "--repo", str(repo),
        "--max-tokens", str(max_tokens),
        "--plan-mode", plan_mode,
        "--max-repair-attempts", str(max_repair_attempts),
        "--provider", provider,
    ]
    args.append("--stream" if stream else "--no-stream")
    if thinking is not None:
        args.extend(["--thinking", thinking])
    if unsafe:
        args.append("--unsafe")
    if verbose:
        args.append("--verbose")
    if extra_json is not None:
        args.extend(["--extra-json", extra_json])
    if redact:
        args.append("--redact")
    if dangerously_skip_permissions:
        args.append("--dangerously-skip-permissions")
    if auto_accept_edits:
        args.append("--auto-accept-edits")
    if auto_approve:
        args.append("--auto-approve")
    if small_model:
        args.append("--small-model")
    if api_key is not None:
        args.extend(["--api-key", api_key])
    if benchmark_runtime_json is not None:
        args.extend(["--benchmark-runtime-json", benchmark_runtime_json])
    if debug is not None:
        args.extend(["--debug", debug])
    if debug_dir is not None:
        args.extend(["--debug-dir", str(debug_dir)])
    args.extend(passthrough_args)
    return args


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    base_url: Optional[str] = typer.Option(None, "--base-url"),
    model: Optional[str] = typer.Option(None, "--model"),
    repo: Path = typer.Option(Path("."), "--repo"),
    max_tokens: int = typer.Option(4096, "--max-tokens"),
    small_model: bool = typer.Option(False, "--small-model"),
    provider: Literal["anthropic", "openai"] = typer.Option("anthropic", "--provider"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    villani_mode: bool | None = typer.Option(None, "--villani-mode/--no-villani-mode"),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Automatically approve all actions without prompting"),
) -> None:
    if ctx.invoked_subcommand is None:
        if not base_url or not model:
            raise typer.BadParameter("--base-url and --model are required when no subcommand is provided")
        resolved_villani = _resolve_villani_flag(repo, villani_mode)
        _run_interactive(base_url, model, repo, max_tokens, small_model, provider, api_key, villani_mode=resolved_villani, auto_approve=auto_approve)


@app.command()
def run(
    instruction: Annotated[str, typer.Argument(help="User instruction")],
    base_url: BaseUrlOption,
    model: ModelOption,
    repo: RepoOption = Path("."),
    max_tokens: MaxTokensOption = 4096,
    stream: StreamOption = True,
    thinking: ThinkingOption = None,
    unsafe: UnsafeOption = False,
    verbose: VerboseOption = False,
    extra_json: ExtraJsonOption = None,
    redact: RedactOption = False,
    dangerously_skip_permissions: SkipPermissionsOption = False,
    auto_accept_edits: AutoAcceptEditsOption = False,
    auto_approve: AutoApproveOption = False,
    plan_mode: PlanModeOption = "auto",
    max_repair_attempts: MaxRepairAttemptsOption = 2,
    small_model: SmallModelOption = False,
    provider: ProviderOption = "anthropic",
    api_key: ApiKeyOption = None,
    benchmark_runtime_json: BenchmarkRuntimeOption = None,
    debug: DebugOption = None,
    debug_dir: DebugDirOption = None,
    role: str = typer.Option("default", "--role", hidden=True),
    result_json_path: Optional[Path] = typer.Option(None, "--result-json-path", hidden=True),
    parent_mission_id: Optional[str] = typer.Option(None, "--parent-mission-id", hidden=True),
) -> None:
    debug_mode = DebugMode(build_debug_config(debug).mode.value)
    runner = _build_runner(base_url, model, repo, max_tokens, stream, thinking, unsafe, verbose, extra_json, redact, dangerously_skip_permissions, auto_accept_edits, auto_approve, plan_mode, max_repair_attempts, small_model, provider, api_key, benchmark_runtime_json=benchmark_runtime_json, debug_mode=debug_mode, debug_dir=debug_dir)
    if auto_approve:
        console.print("Auto-approval: ON")
    result = runner.run(instruction)
    if parent_mission_id:
        set_current_mission_id(repo.resolve(), parent_mission_id)
    if result_json_path:
        _write_result_artifact(result_json_path, role=role, result=result)
        return
    _print_response_text_blocks(result)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def orchestrate(
    ctx: typer.Context,
    instruction: Annotated[str, typer.Argument(help="User instruction")],
    base_url: BaseUrlOption,
    model: ModelOption,
    repo: RepoOption = Path("."),
    max_tokens: MaxTokensOption = 4096,
    stream: StreamOption = True,
    thinking: ThinkingOption = None,
    unsafe: UnsafeOption = False,
    verbose: VerboseOption = False,
    extra_json: ExtraJsonOption = None,
    redact: RedactOption = False,
    dangerously_skip_permissions: SkipPermissionsOption = False,
    auto_accept_edits: AutoAcceptEditsOption = False,
    auto_approve: AutoApproveOption = False,
    plan_mode: PlanModeOption = "auto",
    max_repair_attempts: MaxRepairAttemptsOption = 2,
    small_model: SmallModelOption = False,
    provider: ProviderOption = "anthropic",
    api_key: ApiKeyOption = None,
    benchmark_runtime_json: BenchmarkRuntimeOption = None,
    debug: DebugOption = None,
    debug_dir: DebugDirOption = None,
    max_workers: int = typer.Option(3, "--max-workers"),
    max_worker_retries: int = typer.Option(1, "--max-worker-retries"),
    supervisor_timeout_seconds: Optional[int] = typer.Option(None, "--supervisor-timeout-seconds"),
    worker_timeout_seconds: Optional[int] = typer.Option(None, "--worker-timeout-seconds"),
) -> None:
    inherited_args = _build_inherited_run_args(
        base_url=base_url,
        model=model,
        repo=repo,
        max_tokens=max_tokens,
        stream=stream,
        thinking=thinking,
        unsafe=unsafe,
        verbose=verbose,
        extra_json=extra_json,
        redact=redact,
        dangerously_skip_permissions=dangerously_skip_permissions,
        auto_accept_edits=auto_accept_edits,
        auto_approve=auto_approve,
        plan_mode=plan_mode,
        max_repair_attempts=max_repair_attempts,
        small_model=small_model,
        provider=provider,
        api_key=api_key,
        benchmark_runtime_json=benchmark_runtime_json,
        debug=debug,
        debug_dir=debug_dir,
        passthrough_args=list(ctx.args),
    )
    summary = run_orchestrator(
        OrchestratorConfig(
            instruction=instruction,
            repo=repo,
            inherited_run_args=inherited_args,
            max_workers=max_workers,
            max_worker_retries=max_worker_retries,
            supervisor_timeout_seconds=supervisor_timeout_seconds,
            worker_timeout_seconds=worker_timeout_seconds,
        )
    )
    console.print(json.dumps(summary, indent=2))


@app.command()
def interactive(
    base_url: str = typer.Option(..., "--base-url"),
    model: str = typer.Option(..., "--model"),
    repo: Path = typer.Option(Path("."), "--repo"),
    max_tokens: int = typer.Option(4096, "--max-tokens"),
    small_model: bool = typer.Option(False, "--small-model"),
    provider: Literal["anthropic", "openai"] = typer.Option("anthropic", "--provider"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    villani_mode: bool | None = typer.Option(None, "--villani-mode/--no-villani-mode"),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Automatically approve all actions without prompting"),
    debug: Optional[str] = typer.Option(None, "--debug", flag_value="normal"),
    debug_dir: Optional[Path] = typer.Option(None, "--debug-dir"),
    takeover: bool = typer.Option(False, "--takeover", hidden=True),
    objective: Optional[str] = typer.Argument(None),
):
    resolved_villani = takeover or _resolve_villani_flag(repo, villani_mode)
    debug_mode = DebugMode(build_debug_config(debug).mode.value)
    _run_interactive(base_url, model, repo, max_tokens, small_model, provider, api_key, villani_mode=resolved_villani, villani_objective=objective, auto_approve=auto_approve, debug_mode=debug_mode, debug_dir=debug_dir)


@app.command("villani-mode")
def villani_mode_cmd(
    objective: Optional[str] = typer.Argument(None, help="Optional steering objective"),
    base_url: str = typer.Option(..., "--base-url"),
    model: str = typer.Option(..., "--model"),
    repo: Path = typer.Option(Path("."), "--repo"),
    max_tokens: int = typer.Option(4096, "--max-tokens"),
    small_model: bool = typer.Option(False, "--small-model"),
    provider: Literal["anthropic", "openai"] = typer.Option("anthropic", "--provider"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Automatically approve all actions without prompting"),
    debug: Optional[str] = typer.Option(None, "--debug", flag_value="normal"),
    debug_dir: Optional[Path] = typer.Option(None, "--debug-dir"),
) -> None:
    debug_mode = DebugMode(build_debug_config(debug).mode.value)
    _run_interactive(base_url, model, repo, max_tokens, small_model, provider, api_key, villani_mode=True, villani_objective=objective, auto_approve=auto_approve, debug_mode=debug_mode, debug_dir=debug_dir)


@app.command("takeover", hidden=True)
def takeover_cmd(
    objective: Optional[str] = typer.Argument(None, help="Optional Villani mode objective"),
    base_url: str = typer.Option(..., "--base-url"),
    model: str = typer.Option(..., "--model"),
    repo: Path = typer.Option(Path("."), "--repo"),
    max_tokens: int = typer.Option(4096, "--max-tokens"),
    small_model: bool = typer.Option(False, "--small-model"),
    provider: Literal["anthropic", "openai"] = typer.Option("anthropic", "--provider"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Automatically approve all actions without prompting"),
) -> None:
    runner = _build_runner(base_url, model, repo, max_tokens, True, None, False, False, None, False, False, False, auto_approve, "auto", 2, small_model, provider, api_key, villani_mode=True, villani_objective=objective)
    if auto_approve:
        console.print("Auto-approval: ON")
    result = runner.run_villani_mode()
    _print_response_text_blocks(result)


@app.command()
def init(
    repo: Path = typer.Option(Path("."), "--repo", help="Repository path"),
) -> None:
    from villani_code.project_memory import init_project_memory

    files = init_project_memory(repo.resolve())
    console.print("Initialized .villani project memory:")
    for key, path in files.items():
        console.print(f"- {key}: {path}")


@app.command("debug-bundle")
def debug_bundle_cmd(
    repo: Path = typer.Option(Path("."), "--repo", help="Repository path"),
    mission_id: Optional[str] = typer.Option(None, "--mission-id", help="Mission id (defaults to current)"),
) -> None:
    bundle = create_debug_bundle(repo.resolve(), mission_id=mission_id)
    console.print(str(bundle))




@trace_app.command("rebuild-summary")
def trace_rebuild_summary_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", exists=True, file_okay=False, dir_okay=True, resolve_path=True),
) -> None:
    path = write_summary_from_events(run_dir)
    console.print(str(path))


@trace_app.command("rebuild-tool-calls")
def trace_rebuild_tool_calls_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", exists=True, file_okay=False, dir_okay=True, resolve_path=True),
) -> None:
    path = write_tool_calls_from_events(run_dir)
    console.print(str(path))


@app.command("context")
def context_cmd(
    repo: Path = typer.Option(Path("."), "--repo", help="Repository path"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable context inventory"),
) -> None:
    manager = ContextGovernanceManager(repo.resolve())
    inventory = manager.load_inventory()
    payload = manager._to_dict(inventory)
    if json_output:
        console.print_json(json.dumps(payload))
        return
    console.print(f"Task: {inventory.task_id}")
    budget = inventory.budget
    if budget:
        console.print(f"Pressure: {budget.pressure_level.value} ({budget.total_units}/{budget.budget_limit})")
    console.print("Active context:")
    for item in inventory.active_items:
        console.print(f"- {item.source_id} [{item.source_type}] reason={item.included_reason.value if item.included_reason else '-'} pressure={item.pressure_share}")
    console.print("Excluded candidates:")
    for item in inventory.excluded_items[-20:]:
        console.print(f"- {item.source_id} excluded={item.excluded_reason.value if item.excluded_reason else '-'} why={item.why}")


@app.command("checkpoint")
def checkpoint_cmd(
    task_summary: str = typer.Argument("manual checkpoint"),
    repo: Path = typer.Option(Path("."), "--repo", help="Repository path"),
) -> None:
    manager = ContextGovernanceManager(repo.resolve())
    inventory = manager.load_inventory()
    checkpoint = manager.create_checkpoint(inventory, task_summary, ["manual checkpoint from CLI"])
    console.print(f"Created checkpoint {checkpoint.checkpoint_id}")


@app.command("reset-from-checkpoint")
def reset_from_checkpoint_cmd(
    checkpoint_id: str = typer.Argument(...),
    repo: Path = typer.Option(Path("."), "--repo", help="Repository path"),
) -> None:
    manager = ContextGovernanceManager(repo.resolve())
    checkpoint = manager.reset_from_checkpoint(checkpoint_id)
    console.print(f"Reset context from checkpoint {checkpoint.checkpoint_id}")



register_benchmark_commands(benchmark_app, console)
register_mcp_commands(mcp_app, console)
register_plugin_commands(plugin_app, console)


if __name__ == "__main__":
    app()
