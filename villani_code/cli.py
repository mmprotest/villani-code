from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Optional

import typer
from rich.console import Console

from villani_code.interrupts import InterruptController
from villani_code.optional_tui import OptionalTUIDependencyError, TUI_INSTALL_HINT

from villani_code.anthropic_client import AnthropicClient
from villani_code.openai_client import OpenAIClient
from villani_code.plugins import PluginManager
from villani_code.runtime_safety import ensure_runtime_dependencies_not_shadowed
from villani_code.state import Runner
from villani_code.context_governance import ContextGovernanceManager
from villani_code.benchmark.runner import BenchmarkRunner
from villani_code.benchmark.health import run_healthcheck, validate_tasks
from villani_code.benchmark.reporting import diagnostics, load_results, paired_compare, render_summary_table, write_html_report, write_markdown_report

app = typer.Typer(help="Villani: constrained-inference coding agent with visible context governance")
mcp_app = typer.Typer(help="Manage MCP servers")
plugin_app = typer.Typer(help="Manage local plugins")
benchmark_app = typer.Typer(help="Objective repository benchmark tasks")
app.add_typer(mcp_app, name="mcp")
app.add_typer(plugin_app, name="plugin")
app.add_typer(benchmark_app, name="benchmark")
console = Console()


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


def _build_runner(base_url: str, model: str, repo: Path, max_tokens: int, stream: bool, thinking: Optional[str], unsafe: bool, verbose: bool, extra_json: Optional[str], redact: bool, dangerously_skip_permissions: bool, auto_accept_edits: bool, plan_mode: Literal["off", "auto", "strict"], max_repair_attempts: int, small_model: bool, provider: Literal["anthropic", "openai"], api_key: Optional[str], villani_mode: bool = False, villani_objective: str | None = None) -> Runner:
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
    return Runner(client=client, repo=resolved_repo, model=model, max_tokens=max_tokens, stream=stream, thinking=thinking_obj, unsafe=unsafe, verbose=verbose, extra_json=extra_json, redact=redact, bypass_permissions=dangerously_skip_permissions, auto_accept_edits=auto_accept_edits, plan_mode=plan_mode, max_repair_attempts=max_repair_attempts, small_model=small_model, villani_mode=villani_mode, villani_objective=villani_objective)


def _run_interactive(base_url: str, model: str, repo: Path, max_tokens: int, small_model: bool, provider: Literal["anthropic", "openai"], api_key: Optional[str], villani_mode: bool = False, villani_objective: str | None = None) -> None:
    runner = _build_runner(base_url, model, repo, max_tokens, True, None, False, False, None, False, False, False, "auto", 2, small_model, provider, api_key, villani_mode=villani_mode, villani_objective=villani_objective)
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
) -> None:
    if ctx.invoked_subcommand is None:
        if not base_url or not model:
            raise typer.BadParameter("--base-url and --model are required when no subcommand is provided")
        resolved_villani = _resolve_villani_flag(repo, villani_mode)
        _run_interactive(base_url, model, repo, max_tokens, small_model, provider, api_key, villani_mode=resolved_villani)


@app.command()
def run(
    instruction: str = typer.Argument(..., help="User instruction"),
    base_url: str = typer.Option(..., "--base-url", help="Base URL for compatible messages API server"),
    model: str = typer.Option(..., "--model", help="Model name"),
    repo: Path = typer.Option(Path("."), "--repo", help="Repository path"),
    max_tokens: int = typer.Option(4096, "--max-tokens"),
    stream: bool = typer.Option(True, "--stream/--no-stream"),
    thinking: Optional[str] = typer.Option(None, "--thinking"),
    unsafe: bool = typer.Option(False, "--unsafe"),
    verbose: bool = typer.Option(False, "--verbose"),
    extra_json: Optional[str] = typer.Option(None, "--extra-json"),
    redact: bool = typer.Option(False, "--redact"),
    dangerously_skip_permissions: bool = typer.Option(False, "--dangerously-skip-permissions"),
    auto_accept_edits: bool = typer.Option(False, "--auto-accept-edits"),
    plan_mode: Literal["off", "auto", "strict"] = typer.Option("auto", "--plan-mode"),
    max_repair_attempts: int = typer.Option(2, "--max-repair-attempts"),
    small_model: bool = typer.Option(False, "--small-model"),
    provider: Literal["anthropic", "openai"] = typer.Option("anthropic", "--provider"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    runner = _build_runner(base_url, model, repo, max_tokens, stream, thinking, unsafe, verbose, extra_json, redact, dangerously_skip_permissions, auto_accept_edits, plan_mode, max_repair_attempts, small_model, provider, api_key)
    result = runner.run(instruction)
    _print_response_text_blocks(result)


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
    takeover: bool = typer.Option(False, "--takeover", hidden=True),
    objective: Optional[str] = typer.Argument(None),
):
    resolved_villani = takeover or _resolve_villani_flag(repo, villani_mode)
    _run_interactive(base_url, model, repo, max_tokens, small_model, provider, api_key, villani_mode=resolved_villani, villani_objective=objective)


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
) -> None:
    _run_interactive(base_url, model, repo, max_tokens, small_model, provider, api_key, villani_mode=True, villani_objective=objective)


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
) -> None:
    runner = _build_runner(base_url, model, repo, max_tokens, True, None, False, False, None, False, False, False, "auto", 2, small_model, provider, api_key, villani_mode=True, villani_objective=objective)
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


@benchmark_app.command("list")
def benchmark_list_cmd(
    suite: Path = typer.Option(Path("benchmark_tasks/villani_bench_v1"), "--suite"),
    private_suite: Optional[Path] = typer.Option(None, "--private-suite"),
    family: Optional[str] = typer.Option(None, "--family"),
    difficulty: Optional[str] = typer.Option(None, "--difficulty"),
    tag: Optional[str] = typer.Option(None, "--tag"),
    source_type: Optional[str] = typer.Option(None, "--source-type"),
    language: Optional[str] = typer.Option(None, "--language"),
    track: Optional[str] = typer.Option(None, "--track"),
    include_private: bool = typer.Option(False, "--include-private"),
) -> None:
    runner = BenchmarkRunner(output_dir=Path("artifacts/benchmark"), private_suite_dir=private_suite.resolve() if private_suite else None)
    tasks = runner.list_tasks(
        suite.resolve(),
        include_private=include_private,
        family=family,
        difficulty=difficulty,
        tag=tag,
        source_type=source_type,
        language=language,
        track=track,
    )
    payload = [
        {
            "id": task.id,
            "track": task.benchmark_track.value,
            "family": task.family.value,
            "difficulty": task.difficulty.value,
            "source_type": task.source_type.value,
            "language": task.language,
            "tags": task.tags,
        }
        for task in tasks
    ]
    console.print_json(json.dumps(payload))


@benchmark_app.command("run")
def benchmark_run_cmd(
    suite: Path = typer.Option(Path("benchmark_tasks/villani_bench_v1"), "--suite"),
    private_suite: Optional[Path] = typer.Option(None, "--private-suite"),
    task: Optional[str] = typer.Option(None, "--task"),
    agent: list[str] = typer.Option(["villani"], "--agent", help="Benchmark agent(s): villani, aider, opencode. Repeat --agent to compare multiple agents."),
    model: Optional[str] = typer.Option(None, "--model"),
    base_url: Optional[str] = typer.Option(None, "--base-url"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    provider: Optional[Literal["anthropic", "openai"]] = typer.Option(None, "--provider", help="Provider for benchmark agents. For fair local same-model comparisons, use --provider openai with --base-url and --model."),
    output_dir: Path = typer.Option(Path("artifacts/benchmark"), "--output-dir"),
    keep_workspace: bool = typer.Option(False, "--keep-workspace"),
    repeat: int = typer.Option(1, "--repeat"),
    family: Optional[str] = typer.Option(None, "--family"),
    difficulty: Optional[str] = typer.Option(None, "--difficulty"),
    tag: Optional[str] = typer.Option(None, "--tag"),
    source_type: Optional[str] = typer.Option(None, "--source-type"),
    language: Optional[str] = typer.Option(None, "--language"),
    track: Optional[str] = typer.Option(None, "--track"),
    include_private: bool = typer.Option(False, "--include-private"),
) -> None:
    runner = BenchmarkRunner(
        output_dir=output_dir.resolve(),
        keep_workspace=keep_workspace,
        private_suite_dir=private_suite.resolve() if private_suite else None,
    )
    results_by_agent: dict[str, object] = {}
    for agent_name in agent:
        results_by_agent[agent_name] = runner.run(
            suite_dir=suite.resolve(),
            task_id=task,
            agent=agent_name,
            model=model,
            base_url=base_url,
            api_key=api_key,
            provider=provider,
            repeat=repeat,
            include_private=include_private,
            family=family,
            difficulty=difficulty,
            tag=tag,
            source_type=source_type,
            language=language,
            track=track,
        )
    console.print_json(json.dumps(results_by_agent if len(agent) > 1 else results_by_agent[agent[0]]))


@benchmark_app.command("summary")
def benchmark_summary_cmd(results: Path = typer.Option(..., "--results")) -> None:
    rows = load_results(results.resolve())
    console.print(render_summary_table(rows))


@benchmark_app.command("stats")
def benchmark_stats_cmd(results: Path = typer.Option(..., "--results")) -> None:
    rows = load_results(results.resolve())
    console.print_json(json.dumps(diagnostics(rows)))


@benchmark_app.command("compare")
def benchmark_compare_cmd(
    results_a: Path = typer.Option(..., "--results-a"),
    results_b: Path = typer.Option(..., "--results-b"),
) -> None:
    a = load_results(results_a.resolve())
    b = load_results(results_b.resolve())
    console.print_json(json.dumps(paired_compare(a, b)))


@benchmark_app.command("report")
def benchmark_report_cmd(
    results: Path = typer.Option(..., "--results"),
    out: Path = typer.Option(Path("artifacts/benchmark/report.md"), "--out"),
    format: str = typer.Option("markdown", "--format"),
) -> None:
    rows = load_results(results.resolve())
    if format == "html":
        write_html_report(rows, out.resolve())
    else:
        write_markdown_report(rows, out.resolve())
    console.print(f"wrote {out}")


@benchmark_app.command("healthcheck")
def benchmark_healthcheck_cmd(
    suite: Path = typer.Option(Path("benchmark_tasks/villani_bench_v1"), "--suite"),
) -> None:
    health = run_healthcheck(suite.resolve())
    console.print_json(json.dumps(health))
    if not health.get("ok", False):
        raise typer.Exit(code=1)


@benchmark_app.command("validate-tasks")
def benchmark_validate_tasks_cmd(
    suite: Path = typer.Option(Path("benchmark_tasks/villani_bench_v1"), "--suite"),
) -> None:
    payload = validate_tasks(suite.resolve())
    console.print_json(json.dumps(payload))
    if not payload.get("ok", False):
        raise typer.Exit(code=1)


@benchmark_app.command("manifest")
def benchmark_manifest_cmd(results: Path = typer.Option(..., "--results")) -> None:
    rows = load_results(results.resolve())
    manifests = [r.reproducibility_manifest_path for r in rows]
    console.print_json(json.dumps({"manifests": manifests}))


@mcp_app.command("list")
def mcp_list(repo: Path = typer.Option(Path("."), "--repo")):
    from villani_code.mcp import load_mcp_config

    console.print_json(json.dumps(load_mcp_config(repo.resolve())))


@mcp_app.command("add")
def mcp_add(name: str, server_type: str, endpoint: str, repo: Path = typer.Option(Path("."), "--repo")):
    cfg_path = repo.resolve() / ".mcp.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {"servers": {}}
    cfg.setdefault("servers", {})[name] = {"type": server_type, "endpoint": endpoint}
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    console.print(f"Added MCP server {name}")


@mcp_app.command("remove")
def mcp_remove(name: str, repo: Path = typer.Option(Path("."), "--repo")):
    cfg_path = repo.resolve() / ".mcp.json"
    if not cfg_path.exists():
        return
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.get("servers", {}).pop(name, None)
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    console.print(f"Removed MCP server {name}")


@mcp_app.command("reset-project-choices")
def mcp_reset_project_choices(repo: Path = typer.Option(Path("."), "--repo")):
    p = repo.resolve() / ".villani_code" / "mcp_approvals.json"
    if p.exists():
        p.unlink()
    console.print("Reset project MCP approvals")


@plugin_app.command("install")
def plugin_install(path: Path, repo: Path = typer.Option(Path("."), "--repo")):
    pm = PluginManager(repo.resolve())
    console.print(f"Installed {pm.install(path.resolve())}")


@plugin_app.command("list")
def plugin_list(repo: Path = typer.Option(Path("."), "--repo")):
    pm = PluginManager(repo.resolve())
    for name in pm.list():
        console.print(name)


@plugin_app.command("remove")
def plugin_remove(name: str, repo: Path = typer.Option(Path("."), "--repo")):
    pm = PluginManager(repo.resolve())
    pm.remove(name)
    console.print(f"Removed {name}")


if __name__ == "__main__":
    app()
