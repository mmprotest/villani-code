from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

import typer
from rich.console import Console

from villani_code.benchmark.health import run_healthcheck, validate_tasks
from villani_code.benchmark.rebuild import rebuild_results_from_directory
from villani_code.benchmark.reporting import (
    diagnostics,
    load_results,
    paired_compare,
    render_summary_table,
    write_html_report,
    write_markdown_report,
)
from villani_code.benchmark.runner import BenchmarkRunner
from villani_code.plugins import PluginManager


def register_benchmark_commands(benchmark_app: typer.Typer, console: Console) -> None:
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
        runner = BenchmarkRunner(
            output_dir=Path("artifacts/benchmark"),
            private_suite_dir=private_suite.resolve() if private_suite else None,
        )
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
        agent: list[str] = typer.Option(
            ["villani"],
            "--agent",
            help=(
                "Benchmark agent(s): villani, aider, opencode, claude-code. "
                "Repeat --agent to compare multiple agents."
            ),
        ),
        model: Optional[str] = typer.Option(None, "--model"),
        base_url: Optional[str] = typer.Option(None, "--base-url"),
        api_key: Optional[str] = typer.Option(None, "--api-key"),
        provider: Optional[Literal["anthropic", "openai"]] = typer.Option(
            None,
            "--provider",
            help=(
                "Provider for benchmark agents. For fair local same-model comparisons, "
                "use --provider openai with --base-url and --model."
            ),
        ),
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
        console.print_json(
            json.dumps(results_by_agent if len(agent) > 1 else results_by_agent[agent[0]])
        )


    @benchmark_app.command("rebuild-results")
    def benchmark_rebuild_results_cmd(
        dir: Path = typer.Option(..., "--dir"),
        suite: Path = typer.Option(Path("benchmark_tasks/villani_bench_v1"), "--suite"),
    ) -> None:
        summary = rebuild_results_from_directory(dir.resolve(), task_suite_roots=[suite.resolve()])
        quality = "exact" if summary.partial_count == 0 else "partial"
        console.print(
            f"rebuilt {summary.rebuilt_count} results in {summary.output_dir} "
            f"(exact={summary.exact_count}, partial={summary.partial_count}, quality={quality})"
        )
        if summary.missing_common_fields:
            console.print("common missing fields: " + ", ".join(summary.missing_common_fields))
        if summary.warnings:
            console.print(f"warnings: {len(summary.warnings)} (see rebuild_meta.json)")

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


def register_mcp_commands(mcp_app: typer.Typer, console: Console) -> None:
    @mcp_app.command("list")
    def mcp_list(repo: Path = typer.Option(Path("."), "--repo")) -> None:
        from villani_code.mcp import load_mcp_config

        console.print_json(json.dumps(load_mcp_config(repo.resolve())))

    @mcp_app.command("add")
    def mcp_add(
        name: str,
        server_type: str,
        endpoint: str,
        repo: Path = typer.Option(Path("."), "--repo"),
    ) -> None:
        cfg_path = repo.resolve() / ".mcp.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {"servers": {}}
        cfg.setdefault("servers", {})[name] = {"type": server_type, "endpoint": endpoint}
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        console.print(f"Added MCP server {name}")

    @mcp_app.command("remove")
    def mcp_remove(name: str, repo: Path = typer.Option(Path("."), "--repo")) -> None:
        cfg_path = repo.resolve() / ".mcp.json"
        if not cfg_path.exists():
            return
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg.get("servers", {}).pop(name, None)
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        console.print(f"Removed MCP server {name}")

    @mcp_app.command("reset-project-choices")
    def mcp_reset_project_choices(repo: Path = typer.Option(Path("."), "--repo")) -> None:
        approvals_file = repo.resolve() / ".villani_code" / "mcp_approvals.json"
        if approvals_file.exists():
            approvals_file.unlink()
        console.print("Reset project MCP approvals")


def register_plugin_commands(plugin_app: typer.Typer, console: Console) -> None:
    @plugin_app.command("install")
    def plugin_install(path: Path, repo: Path = typer.Option(Path("."), "--repo")) -> None:
        manager = PluginManager(repo.resolve())
        console.print(f"Installed {manager.install(path.resolve())}")

    @plugin_app.command("list")
    def plugin_list(repo: Path = typer.Option(Path("."), "--repo")) -> None:
        manager = PluginManager(repo.resolve())
        for name in manager.list():
            console.print(name)

    @plugin_app.command("remove")
    def plugin_remove(name: str, repo: Path = typer.Option(Path("."), "--repo")) -> None:
        manager = PluginManager(repo.resolve())
        manager.remove(name)
        console.print(f"Removed {name}")
