from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Optional

import typer
from rich.console import Console

from ui.settings import SettingsManager
from villani_code.anthropic_client import AnthropicClient
from villani_code.interactive import InteractiveShell
from villani_code.openai_client import OpenAIClient
from villani_code.plugins import PluginManager
from villani_code.state import Runner

app = typer.Typer(help="Villani Code terminal agent runner")
mcp_app = typer.Typer(help="Manage MCP servers")
plugin_app = typer.Typer(help="Manage local plugins")
app.add_typer(mcp_app, name="mcp")
app.add_typer(plugin_app, name="plugin")
console = Console()


def _resolve_villani_flag(repo: Path, cli_value: bool | None) -> bool:
    if cli_value is not None:
        return cli_value
    settings = SettingsManager(repo.resolve()).load()
    return bool(getattr(settings, "villani_mode", False))


def _build_runner(base_url: str, model: str, repo: Path, max_tokens: int, stream: bool, thinking: Optional[str], unsafe: bool, verbose: bool, extra_json: Optional[str], redact: bool, dangerously_skip_permissions: bool, auto_accept_edits: bool, plan_mode: bool, small_model: bool, provider: Literal["anthropic", "openai"], api_key: Optional[str], villani_mode: bool = False, villani_objective: str | None = None) -> Runner:
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
    return Runner(client=client, repo=repo.resolve(), model=model, max_tokens=max_tokens, stream=stream, thinking=thinking_obj, unsafe=unsafe, verbose=verbose, extra_json=extra_json, redact=redact, bypass_permissions=dangerously_skip_permissions, auto_accept_edits=auto_accept_edits, plan_mode=plan_mode, small_model=small_model, villani_mode=villani_mode, villani_objective=villani_objective)


def _run_interactive(base_url: str, model: str, repo: Path, max_tokens: int, small_model: bool, provider: Literal["anthropic", "openai"], api_key: Optional[str], villani_mode: bool = False, villani_objective: str | None = None) -> None:
    runner = _build_runner(base_url, model, repo, max_tokens, True, None, False, False, None, False, False, False, False, small_model, provider, api_key, villani_mode=villani_mode, villani_objective=villani_objective)
    InteractiveShell(runner, repo.resolve(), villani_mode=villani_mode, villani_objective=villani_objective).run()


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
    plan_mode: bool = typer.Option(False, "--plan-mode"),
    small_model: bool = typer.Option(False, "--small-model"),
    provider: Literal["anthropic", "openai"] = typer.Option("anthropic", "--provider"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    runner = _build_runner(base_url, model, repo, max_tokens, stream, thinking, unsafe, verbose, extra_json, redact, dangerously_skip_permissions, auto_accept_edits, plan_mode, small_model, provider, api_key)
    result = runner.run(instruction)
    for block in result["response"].get("content", []):
        if block.get("type") == "text":
            console.print(block.get("text", ""))


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
    objective: Optional[str] = typer.Argument(None),
):
    resolved_villani = _resolve_villani_flag(repo, villani_mode)
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
