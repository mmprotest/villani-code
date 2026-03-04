from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from villani_code.anthropic_client import AnthropicClient
from villani_code.interactive import InteractiveShell
from villani_code.plugins import PluginManager
from villani_code.state import Runner

app = typer.Typer(help="Villani Code terminal agent runner")
mcp_app = typer.Typer(help="Manage MCP servers")
plugin_app = typer.Typer(help="Manage local plugins")
app.add_typer(mcp_app, name="mcp")
app.add_typer(plugin_app, name="plugin")
console = Console()


def _build_runner(base_url: str, model: str, repo: Path, max_tokens: int, stream: bool, thinking: Optional[str], unsafe: bool, verbose: bool, extra_json: Optional[str], redact: bool, dangerously_skip_permissions: bool, auto_accept_edits: bool, plan_mode: bool, max_prompt_chars: int) -> Runner:
    client = AnthropicClient(base_url=base_url)
    thinking_obj = None
    if thinking:
        try:
            thinking_obj = json.loads(thinking)
        except json.JSONDecodeError:
            thinking_obj = thinking
    return Runner(client=client, repo=repo.resolve(), model=model, max_tokens=max_tokens, stream=stream, thinking=thinking_obj, unsafe=unsafe, verbose=verbose, extra_json=extra_json, redact=redact, bypass_permissions=dangerously_skip_permissions, auto_accept_edits=auto_accept_edits, plan_mode=plan_mode, max_prompt_chars=max_prompt_chars)


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
    max_prompt_chars: int = typer.Option(200000, "--max-prompt-chars"),
) -> None:
    runner = _build_runner(base_url, model, repo, max_tokens, stream, thinking, unsafe, verbose, extra_json, redact, dangerously_skip_permissions, auto_accept_edits, plan_mode, max_prompt_chars)
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
    resume: str | None = typer.Option(None, "--resume"),
    max_prompt_chars: int = typer.Option(200000, "--max-prompt-chars"),
):
    runner = _build_runner(base_url, model, repo, max_tokens, True, None, False, False, None, False, False, False, False, max_prompt_chars)
    InteractiveShell(runner, repo.resolve(), base_url=base_url, resume=resume).run()


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
