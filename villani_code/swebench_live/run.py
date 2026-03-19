from __future__ import annotations

import json
import os
import re
import sys
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path

import typer

from villani_code.swebench_live.agents import AgentRunner, VillaniAgentRunner
from villani_code.swebench_live.io_utils import run_logged_subprocess, sanitize_env, shell_join, write_predictions, write_sidecar_logs
from villani_code.swebench_live.prompting import build_default_prompt
from villani_code.swebench_live.types import AgentConfig, AgentInvocationResult, InstanceLogRecord, Platform, ProcessResult, RunConfig, SwebenchLiveInstance

app = typer.Typer(help="Run the Villani agent against SWE-bench-Live instances.")


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _isoformat(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def get_default_image_name(instance_id: str, platform: Platform) -> str:
    med = "x86_64" if platform == "linux" else "win"
    return f"starryzhang/sweb.eval.{med}.{instance_id.replace('__', '_1776_').lower()}"


class SwebenchLivePreparer(ABC):
    @abstractmethod
    def prepare(self, instance: SwebenchLiveInstance, config: RunConfig, log_dir: Path) -> AbstractContextManager["PreparedInstance"]:
        raise NotImplementedError


class PreparedInstance(AbstractContextManager["PreparedInstance"]):
    def __init__(self, *, instance_id: str, container_name: str, platform: Platform, repo_path: str, log_dir: Path) -> None:
        self.instance_id = instance_id
        self.container_name = container_name
        self.platform = platform
        self.repo_path = repo_path
        self.log_dir = log_dir

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()
        return None

    def _docker_exec_command(self, *, cwd: str, env: dict[str, str], command: list[str]) -> list[str]:
        docker_command = ["docker", "exec"]
        if cwd:
            docker_command.extend(["--workdir", cwd])
        for key, value in env.items():
            docker_command.extend(["-e", f"{key}={value}"])
        docker_command.extend([self.container_name, *command])
        return docker_command

    def run_process(
        self,
        *,
        command: list[str],
        cwd: str,
        env: dict[str, str],
        timeout_seconds: int,
        stdout_path: Path,
        stderr_path: Path,
    ) -> AgentInvocationResult:
        process = run_logged_subprocess(
            self._docker_exec_command(cwd=cwd, env=env, command=command),
            cwd=None,
            env=None,
            timeout_seconds=timeout_seconds,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        return AgentInvocationResult(
            exit_code=process.exit_code,
            timed_out=process.timed_out,
            duration_seconds=process.duration_seconds,
            stdout_path=process.stdout_path,
            stderr_path=process.stderr_path,
            command=process.command,
            sanitized_command=process.sanitized_command,
            error_summary=_summarize_process_failure(process),
        )

    def capture_diff(self, log_dir: Path) -> str:
        result = run_logged_subprocess(
            self._docker_exec_command(
                cwd=self.repo_path,
                env={},
                command=["git", "--no-pager", "diff", "HEAD", "--text"],
            ),
            cwd=None,
            env=None,
            timeout_seconds=120,
            stdout_path=log_dir / "git_diff_stdout.txt",
            stderr_path=log_dir / "git_diff_stderr.txt",
        )
        if result.timed_out or (result.exit_code not in {0, None} and result.stdout.strip() == ""):
            raise RuntimeError(result.stderr.strip() or "git diff failed")
        return result.stdout

    def cleanup(self) -> None:
        run_logged_subprocess(
            ["docker", "rm", "-f", self.container_name],
            cwd=None,
            env=None,
            timeout_seconds=120,
            stdout_path=self.log_dir / "cleanup_stdout.txt",
            stderr_path=self.log_dir / "cleanup_stderr.txt",
        )


class DockerLaunchImagePreparer(SwebenchLivePreparer):
    """Prepare fresh SWE-bench-Live instances by starting the official per-instance launch image.

    This driver intentionally uses the official image naming and /testbed layout so that prediction
    generation stays aligned with the public SWE-bench-Live evaluation workflow instead of inventing
    a repo-local substitute harness.
    """

    def prepare(self, instance: SwebenchLiveInstance, config: RunConfig, log_dir: Path) -> PreparedInstance:
        image = instance.docker_image or get_default_image_name(instance.instance_id, config.platform)
        container_name = self._container_name(instance.instance_id)
        repo_path = "/testbed" if config.platform == "linux" else r"C:\testbed"
        self._start_container(image=image, container_name=container_name, platform=config.platform, log_dir=log_dir)
        prepared = PreparedInstance(
            instance_id=instance.instance_id,
            container_name=container_name,
            platform=config.platform,
            repo_path=repo_path,
            log_dir=log_dir,
        )
        resolved_repo_path = self._resolve_repo_path(prepared, default_repo_path=repo_path, log_dir=log_dir)
        prepared.repo_path = resolved_repo_path
        self._install_villani(prepared=prepared, config=config, log_dir=log_dir)
        return prepared

    @staticmethod
    def _container_name(instance_id: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", instance_id).strip("-").lower() or "instance"
        return f"villani-swebench-{slug}-{uuid.uuid4().hex[:8]}"

    def _start_container(self, *, image: str, container_name: str, platform: Platform, log_dir: Path) -> None:
        pull = run_logged_subprocess(
            ["docker", "pull", image],
            cwd=None,
            env=None,
            timeout_seconds=1800,
            stdout_path=log_dir / "docker_pull_stdout.txt",
            stderr_path=log_dir / "docker_pull_stderr.txt",
        )
        if pull.timed_out or pull.exit_code != 0:
            raise RuntimeError(f"docker pull failed for {image}: {pull.stderr.strip() or pull.stdout.strip()}")
        keepalive_command = ["tail", "-f", "/dev/null"] if platform == "linux" else ["powershell", "-NoProfile", "-Command", "while ($true) { Start-Sleep -Seconds 3600 }"]
        start = run_logged_subprocess(
            ["docker", "run", "-d", "--rm", "--name", container_name, image, *keepalive_command],
            cwd=None,
            env=None,
            timeout_seconds=120,
            stdout_path=log_dir / "docker_run_stdout.txt",
            stderr_path=log_dir / "docker_run_stderr.txt",
        )
        if start.timed_out or start.exit_code != 0:
            raise RuntimeError(f"docker run failed for {image}: {start.stderr.strip() or start.stdout.strip()}")

    def _resolve_repo_path(self, prepared: PreparedInstance, *, default_repo_path: str, log_dir: Path) -> str:
        if prepared.platform == "linux":
            probe = [
                "bash",
                "-lc",
                "if [ -d /testbed/.git ]; then printf '/testbed'; "
                "else g=$(find /testbed -maxdepth 2 -mindepth 2 -type d -name .git -print -quit); "
                "if [ -n \"$g\" ]; then printf '%s' \"${g%/.git}\"; else printf '/testbed'; fi; fi",
            ]
        else:
            probe = [
                "powershell",
                "-NoProfile",
                "-Command",
                r"if (Test-Path C:\testbed\.git) { Write-Output 'C:\testbed' } else { "
                r"$g = Get-ChildItem -Path C:\testbed -Directory -Recurse -Depth 2 -Force -ErrorAction SilentlyContinue | "
                r"Where-Object { $_.Name -eq '.git' } | Select-Object -First 1; "
                r"if ($g) { Write-Output $g.Parent.FullName } else { Write-Output 'C:\testbed' } }",
            ]
        result = run_logged_subprocess(
            prepared._docker_exec_command(cwd=default_repo_path, env={}, command=probe),
            cwd=None,
            env=None,
            timeout_seconds=60,
            stdout_path=log_dir / "repo_probe_stdout.txt",
            stderr_path=log_dir / "repo_probe_stderr.txt",
        )
        resolved = result.stdout.strip().splitlines()
        return resolved[-1].strip() if resolved and resolved[-1].strip() else default_repo_path

    def _install_villani(self, *, prepared: PreparedInstance, config: RunConfig, log_dir: Path) -> None:
        if not config.install_inside_container:
            return
        install_root = "/opt/villani-code" if prepared.platform == "linux" else r"C:\opt\villani-code"
        copy_result = run_logged_subprocess(
            ["docker", "cp", str(config.villani_source_dir), f"{prepared.container_name}:{install_root}"],
            cwd=None,
            env=None,
            timeout_seconds=600,
            stdout_path=log_dir / "docker_cp_stdout.txt",
            stderr_path=log_dir / "docker_cp_stderr.txt",
        )
        if copy_result.timed_out or copy_result.exit_code != 0:
            raise RuntimeError(copy_result.stderr.strip() or "docker cp failed")
        install_command = ["python", "-m", "pip", "install", "-e", install_root]
        install = run_logged_subprocess(
            prepared._docker_exec_command(cwd=prepared.repo_path, env={}, command=install_command),
            cwd=None,
            env=None,
            timeout_seconds=config.agent.install_timeout_seconds,
            stdout_path=log_dir / "install_stdout.txt",
            stderr_path=log_dir / "install_stderr.txt",
        )
        if install.timed_out or install.exit_code != 0:
            raise RuntimeError(install.stderr.strip() or install.stdout.strip() or "villani install failed")


def _summarize_process_failure(result: ProcessResult) -> str | None:
    if result.timed_out:
        return f"timed out after {result.duration_seconds:.2f}s"
    if result.exit_code in {0, None}:
        return None
    detail = result.stderr.strip() or result.stdout.strip()
    if detail:
        first_line = detail.splitlines()[0].strip()
        if first_line:
            return f"exit code {result.exit_code}: {first_line[:240]}"
    return f"exit code {result.exit_code}"


def _sanitize_log_payload(payload: dict[str, object]) -> dict[str, object]:
    sanitized = dict(payload)
    env = payload.get("env")
    if isinstance(env, dict):
        sanitized["env"] = sanitize_env({str(k): str(v) for k, v in env.items()})
    command = payload.get("command")
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        sanitized["command"] = shell_join(command)
    return sanitized


def _emit_structured_log(payload: dict[str, object]) -> None:
    print(json.dumps(_sanitize_log_payload(payload), sort_keys=True), flush=True)


def _load_dataset_rows(dataset: str, split: str | None) -> list[dict[str, object]]:
    dataset_path = Path(dataset)
    if dataset_path.exists() and dataset_path.suffix == ".jsonl":
        return [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        from datasets import load_dataset
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Loading SWE-bench-Live datasets requires the 'datasets' package. Install it, or pass a local jsonl file."
        ) from exc
    if split is not None:
        dataset_rows = load_dataset(dataset, split=split)
        return [dict(row) for row in dataset_rows]
    loaded = load_dataset(dataset)
    if hasattr(loaded, "keys"):
        rows: list[dict[str, object]] = []
        for key in loaded.keys():
            rows.extend(dict(row) for row in loaded[key])
        return rows
    return [dict(row) for row in loaded]


def load_instances(dataset: str, split: str | None, instance_limit: int | None) -> list[SwebenchLiveInstance]:
    rows = _load_dataset_rows(dataset, split)
    instances = [SwebenchLiveInstance.from_mapping(row) for row in rows]
    if instance_limit is not None:
        return instances[:instance_limit]
    return instances


def _default_agent_config(
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    timeout_seconds: int,
    install_timeout_seconds: int,
) -> AgentConfig:
    resolved_provider = provider or os.environ.get("VILLANI_SWEBENCH_PROVIDER") or os.environ.get("VILLANI_PROVIDER") or "anthropic"
    resolved_model = model or os.environ.get("VILLANI_SWEBENCH_MODEL") or os.environ.get("VILLANI_MODEL")
    if not resolved_model:
        raise typer.BadParameter("--model is required unless VILLANI_SWEBENCH_MODEL or VILLANI_MODEL is set")
    resolved_base_url = base_url or os.environ.get("VILLANI_SWEBENCH_BASE_URL") or os.environ.get("VILLANI_BASE_URL")
    resolved_api_key = api_key or os.environ.get("VILLANI_SWEBENCH_API_KEY")
    if resolved_api_key is None:
        env_var = "OPENAI_API_KEY" if resolved_provider == "openai" else "ANTHROPIC_API_KEY"
        env_value = os.environ.get(env_var)
        if env_value:
            return AgentConfig(
                provider=resolved_provider,
                model=resolved_model,
                base_url=resolved_base_url,
                api_key=None,
                timeout_seconds=timeout_seconds,
                install_timeout_seconds=install_timeout_seconds,
                env_overrides={env_var: env_value},
            )
    return AgentConfig(
        provider=resolved_provider,
        model=resolved_model,
        base_url=resolved_base_url,
        api_key=resolved_api_key,
        timeout_seconds=timeout_seconds,
        install_timeout_seconds=install_timeout_seconds,
        env_overrides={},
    )


def _repo_source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run_benchmark(
    config: RunConfig,
    *,
    instances: Sequence[SwebenchLiveInstance] | None = None,
    preparer: SwebenchLivePreparer | None = None,
    agent_runner: AgentRunner | None = None,
) -> tuple[dict[str, dict[str, str]], list[InstanceLogRecord]]:
    selected_instances = list(instances) if instances is not None else load_instances(config.dataset, config.split, config.instance_limit)
    selected_preparer = preparer or DockerLaunchImagePreparer()
    selected_agent_runner = agent_runner or VillaniAgentRunner()
    predictions: dict[str, dict[str, str]] = {}
    logs: list[InstanceLogRecord] = []

    config.work_dir.mkdir(parents=True, exist_ok=True)
    for instance in selected_instances:
        start_dt = _utc_now()
        log_dir = config.work_dir / instance.instance_id
        log_dir.mkdir(parents=True, exist_ok=True)
        prompt = build_default_prompt(instance.problem_statement)
        (log_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        patch = ""
        error_summary: str | None = None
        agent_result: AgentInvocationResult | None = None

        _emit_structured_log(
            {
                "event": "instance_started",
                "instance_id": instance.instance_id,
                "timestamp": _isoformat(start_dt),
                "platform": config.platform,
                "dataset": config.dataset,
                "split": config.split,
            }
        )
        try:
            with selected_preparer.prepare(instance, config, log_dir) as prepared_instance:
                agent_result = selected_agent_runner.run(prepared_instance, prompt, config.agent, log_dir)
                (log_dir / "agent_command.txt").write_text(shell_join(agent_result.sanitized_command), encoding="utf-8")
                if agent_result.exit_code == 0 and not agent_result.timed_out:
                    patch = prepared_instance.capture_diff(log_dir)
                else:
                    error_summary = agent_result.error_summary or "agent execution failed"
        except Exception as exc:  # noqa: BLE001
            error_summary = str(exc)
        end_dt = _utc_now()
        duration_seconds = (end_dt - start_dt).total_seconds()
        predictions[instance.instance_id] = {"model_patch": patch}
        log_record = InstanceLogRecord(
            instance_id=instance.instance_id,
            start_timestamp=_isoformat(start_dt),
            end_timestamp=_isoformat(end_dt),
            exit_code=agent_result.exit_code if agent_result is not None else None,
            stdout_path=str(agent_result.stdout_path if agent_result is not None else log_dir / "agent_stdout.txt"),
            stderr_path=str(agent_result.stderr_path if agent_result is not None else log_dir / "agent_stderr.txt"),
            patch_byte_size=len(patch.encode("utf-8")),
            duration_seconds=duration_seconds,
            error_summary=error_summary,
            timed_out=bool(agent_result.timed_out) if agent_result is not None else False,
        )
        logs.append(log_record)
        _emit_structured_log(
            {
                "event": "instance_finished",
                "instance_id": instance.instance_id,
                "timestamp": _isoformat(end_dt),
                "exit_code": log_record.exit_code,
                "duration_seconds": duration_seconds,
                "patch_byte_size": log_record.patch_byte_size,
                "error_summary": error_summary,
            }
        )

    write_predictions(predictions, config.output_path)
    if config.logs_path is not None:
        write_sidecar_logs(logs, config.logs_path)
    return predictions, logs


@app.command()
def main(
    dataset: str = typer.Option(..., "--dataset", help="Hugging Face dataset name or local jsonl file."),
    split: str | None = typer.Option(None, "--split", help="Dataset split such as verified, lite, or full."),
    platform: Platform = typer.Option("linux", "--platform"),
    instance_limit: int | None = typer.Option(None, "--instance-limit", min=1),
    output: Path = typer.Option(..., "--output", help="Prediction JSON path."),
    logs_output: Path | None = typer.Option(None, "--logs-output", help="Optional JSONL sidecar logs path."),
    work_dir: Path = typer.Option(Path("artifacts/swebench_live"), "--work-dir", help="Per-instance working log directory."),
    provider: str | None = typer.Option(None, "--provider", help="Villani provider name. Defaults from env when unset."),
    model: str | None = typer.Option(None, "--model", help="Villani model name. Defaults from env when unset."),
    base_url: str | None = typer.Option(None, "--base-url", help="Optional provider base URL."),
    api_key: str | None = typer.Option(None, "--api-key", help="Optional API key. Prefer env vars in shared environments."),
    timeout_seconds: int = typer.Option(3600, "--timeout-seconds", min=1, help="Per-instance Villani timeout in seconds."),
    install_timeout_seconds: int = typer.Option(900, "--install-timeout-seconds", min=1, help="Timeout for installing this package inside the prepared image."),
    villani_source_dir: Path = typer.Option(_repo_source_root(), "--villani-source-dir", help="Local source tree copied into each official launch image."),
) -> None:
    """Generate prediction patches for the official SWE-bench-Live evaluator.

    Notes:
    - The `verified` and `lite` splits are frozen, while `full` is updated over time.
    - This command only generates predictions JSON; users should run the official SWE-bench-Live evaluator afterward.
    - For stable denominators, re-run the official gold patches on your machine before large experiments.
    """

    config = RunConfig(
        dataset=dataset,
        split=split,
        platform=platform,
        instance_limit=instance_limit,
        output_path=output,
        logs_path=logs_output,
        work_dir=work_dir,
        agent=_default_agent_config(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            install_timeout_seconds=install_timeout_seconds,
        ),
        villani_source_dir=villani_source_dir,
        install_inside_container=True,
    )
    run_benchmark(config)


if __name__ == "__main__":
    app()
