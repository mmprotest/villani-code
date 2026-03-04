from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from villani_code.anthropic_client import AnthropicClient
from villani_code.state import Runner

app = typer.Typer(help="Villani Code local agent runner")
console = Console()


@app.command()
def run(
    instruction: str = typer.Argument(..., help="User instruction"),
    base_url: str = typer.Option(..., "--base-url", help="Base URL for local Anthropic-compatible server"),
    model: str = typer.Option(..., "--model", help="Model name"),
    repo: Path = typer.Option(Path("."), "--repo", help="Repository path"),
    max_tokens: int = typer.Option(4096, "--max-tokens"),
    stream: bool = typer.Option(True, "--stream/--no-stream"),
    thinking: Optional[str] = typer.Option(None, "--thinking"),
    unsafe: bool = typer.Option(False, "--unsafe"),
    verbose: bool = typer.Option(False, "--verbose"),
    extra_json: Optional[str] = typer.Option(None, "--extra-json"),
    redact: bool = typer.Option(False, "--redact"),
) -> None:
    """Run the Villani Code tool loop."""
    repo = repo.resolve()
    client = AnthropicClient(base_url=base_url)

    thinking_obj = None
    if thinking:
        try:
            thinking_obj = json.loads(thinking)
        except json.JSONDecodeError:
            thinking_obj = thinking

    runner = Runner(
        client=client,
        repo=repo,
        model=model,
        max_tokens=max_tokens,
        stream=stream,
        thinking=thinking_obj,
        unsafe=unsafe,
        verbose=verbose,
        extra_json=extra_json,
        redact=redact,
    )

    result = runner.run(instruction)
    content = result["response"].get("content", [])
    for block in content:
        if block.get("type") == "text":
            console.print(block.get("text", ""))

    if verbose:
        console.print(f"[dim]Transcript: {result['transcript_path']}[/dim]")
        console.print(f"[dim]Total messages: {len(result['messages'])}[/dim]")


if __name__ == "__main__":
    app()
