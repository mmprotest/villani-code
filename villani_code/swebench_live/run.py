from __future__ import annotations

import json
import os
import re
import shutil
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
from villani_code.swebench_live.types import AgentConfig, AgentInvocationResult, InstanceLogRecord, Platform, ProcessResult, RunConfig, SwebenchLiveInstance, WorkspaceMapping

app = typer.Typer(help="Run the Villani agent against SWE-bench-Live instances.")


BLOCKED_TASK_ENV_INSTALL_SNIPPETS = (
    "pip install villani-code",
    "pip install -e",
    "python -m villani_code.cli",
)


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
    def __init__(
        self,
        *,
        instance_id: str,
        container_name: str,
        platform: Platform,
        task_repo_path: str,
        host_repo_path: Path,
        log_dir: Path,
    ) -> None:
        self.instance_id = instance_id
        self.container_name = container_name
        self.platform = platform
        self.workspace = WorkspaceMapping(host_repo_path=host_repo_path, task_repo_path=task_repo_path)
        self.log_dir = log_dir

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()
        return None

    def _assert_task_command_is_safe(self, command: list[str]) -> None:
        joined = " ".join(command).lower()
        if any(snippet in joined for snippet in BLOCKED_TASK_ENV_INSTALL_SNIPPETS):
            raise RuntimeError(
                "Refusing to install or run villani-code inside the SWE-bench-Live task environment. "
                "Use the external runner configuration instead."
            )

    def _docker_exec_command(self, *, cwd: str, env: dict[str, str], command: list[str]) -> list[str]:
        self._assert_task_command_is_safe(command)
        docker_command = ["docker", "exec"]
        if cwd:
            docker_command.extend(["--workdir", cwd])
        for key, value in env.items():
            docker_command.extend(["-e", f"{key}={value}"])
        docker_command.extend([self.container_name, *command])
        return docker_command

    def task_parent_dir(self) -> str:
        repo_path = self.workspace.task_repo_path
        if self.platform == "windows":
            return str(Path(repo_path).parent).replace("/", "\\")
        return str(Path(repo_path).parent)

    def sync_repo_from_task(self, log_dir: Path) -> None:
        host_repo_path = self.workspace.host_repo_path
        if host_repo_path.exists():
            shutil.rmtree(host_repo_path)
        host_repo_path.parent.mkdir(parents=True, exist_ok=True)
        result = run_logged_subprocess(
            ["docker", "cp", f"{self.container_name}:{self.workspace.task_repo_path}", str(host_repo_path.parent)],
            cwd=None,
            env=None,
            timeout_seconds=600,
            stdout_path=log_dir / "sync_from_task_stdout.txt",
            stderr_path=log_dir / "sync_from_task_stderr.txt",
        )
        if result.timed_out or result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "docker cp from task failed")

    def sync_repo_to_task(self, log_dir: Path) -> None:
        host_repo_path = self.workspace.host_repo_path
        if not host_repo_path.exists():
            raise RuntimeError(
                f"External runner cannot access benchmark repo path: {host_repo_path}. "
                "Check the task-to-host repo path mapping."
            )
        self._remove_task_repo(log_dir)
        result = run_logged_subprocess(
            ["docker", "cp", str(host_repo_path), f"{self.container_name}:{self.task_parent_dir()}"],
            cwd=None,
            env=None,
            timeout_seconds=600,
            stdout_path=log_dir / "sync_to_task_stdout.txt",
            stderr_path=log_dir / "sync_to_task_stderr.txt",
        )
        if result.timed_out or result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "docker cp to task failed")

    def _remove_task_repo(self, log_dir: Path) -> None:
        repo_path = self.workspace.task_repo_path
        if self.platform == "linux":
            command = ["bash", "-lc", f"rm -rf {repo_path}"]
        else:
            command = ["powershell", "-NoProfile", "-Command", f"if (Test-Path '{repo_path}') {{ Remove-Item -Recurse -Force '{repo_path}' }}"]
        result = run_logged_subprocess(
            self._docker_exec_command(cwd=self.task_parent_dir(), env={}, command=command),
            cwd=None,
            env=None,
            timeout_seconds=120,
            stdout_path=log_dir / "remove_task_repo_stdout.txt",
            stderr_path=log_dir / "remove_task_repo_stderr.txt",
        )
        if result.timed_out or result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "failed to replace task repo")

    def capture_diff(self, log_dir: Path) -> str:
        result = run_logged_subprocess(
            self._docker_exec_command(
                cwd=self.workspace.task_repo_path,
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
    """Prepare fresh SWE-bench-Live instances using the official per-instance launch image.

    The task environment stays isolated inside the official image. The Villani runtime executes
    externally from an already-working Python 3.11+ environment against a host-side workspace copy.
    """

    def prepare(self, instance: SwebenchLiveInstance, config: RunConfig, log_dir: Path) -> PreparedInstance:
        image = instance.docker_image or get_default_image_name(instance.instance_id, config.platform)
        container_name = self._container_name(instance.instance_id)
        task_repo_path = "/testbed" if config.platform == "linux" else r"C:\testbed"
        self._start_container(image=image, container_name=container_name, platform=config.platform, log_dir=log_dir)
        resolved_task_repo_path = self._resolve_repo_path(
            container_name=container_name,
            platform=config.platform,
            default_repo_path=task_repo_path,
            log_dir=log_dir,
        )
        prepared = PreparedInstance(
            instance_id=instance.instance_id,
            container_name=container_name,
            platform=config.platform,
            task_repo_path=resolved_task_repo_path,
            host_repo_path=log_dir / "workspace_repo",
            log_dir=log_dir,
        )
        prepared.sync_repo_from_task(log_dir)
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

    def _resolve_repo_path(self, *, container_name: str, platform: Platform, default_repo_path: str, log_dir: Path) -> str:
        if platform == "linux":
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
            ["docker", "exec", "--workdir", default_repo_path, container_name, *probe],
            cwd=None,
            env=None,
            timeout_seconds=60,
            stdout_path=log_dir / "repo_probe_stdout.txt",
            stderr_path=log_dir / "repo_probe_stderr.txt",
        )
        resolved = result.stdout.strip().splitlines()
        return resolved[-1].strip() if resolved and resolved[-1].strip() else default_repo_path


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
    runner_python: str | None,
    runner_command_prefix: str | None,
    runner_cwd: Path | None,
) -> AgentConfig:
    resolved_provider = provider or os.environ.get("VILLANI_SWEBENCH_PROVIDER") or os.environ.get("VILLANI_PROVIDER") or "anthropic"
    resolved_model = model or os.environ.get("VILLANI_SWEBENCH_MODEL") or os.environ.get("VILLANI_MODEL")
    if not resolved_model:
        raise typer.BadParameter("--model is required unless VILLANI_SWEBENCH_MODEL or VILLANI_MODEL is set")
    resolved_base_url = base_url or os.environ.get("VILLANI_SWEBENCH_BASE_URL") or os.environ.get("VILLANI_BASE_URL")
    resolved_api_key = api_key or os.environ.get("VILLANI_SWEBENCH_API_KEY")
    env_overrides: dict[str, str] = {}
    if resolved_api_key is None:
        env_var = "OPENAI_API_KEY" if resolved_provider == "openai" else "ANTHROPIC_API_KEY"
        env_value = os.environ.get(env_var)
        if env_value:
            env_overrides[env_var] = env_value

    resolved_runner_python = runner_python or os.environ.get("VILLANI_SWEBENCH_RUNNER_PYTHON")
    resolved_runner_prefix = runner_command_prefix or os.environ.get("VILLANI_SWEBENCH_RUNNER_COMMAND_PREFIX")
    if resolved_runner_python and resolved_runner_prefix:
        raise typer.BadParameter("Pass only one of --runner-python or --runner-command-prefix")
    runner_prefix = VillaniAgentRunner.parse_runner_command_prefix(resolved_runner_prefix) if resolved_runner_prefix else ()

    return AgentConfig(
        provider=resolved_provider,
        model=resolved_model,
        base_url=resolved_base_url,
        api_key=resolved_api_key,
        timeout_seconds=timeout_seconds,
        env_overrides=env_overrides,
        runner_python=resolved_runner_python,
        runner_command_prefix=runner_prefix,
        runner_cwd=runner_cwd,
    )


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
                prompt = build_default_prompt(
                    instance.problem_statement,
                    accessible_repo_path=str(prepared_instance.workspace.host_repo_path),
                )
                (log_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
                agent_result = selected_agent_runner.run(prepared_instance, prompt, config.agent, log_dir)
                (log_dir / "agent_command.txt").write_text(shell_join(agent_result.sanitized_command), encoding="utf-8")
                if agent_result.exit_code == 0 and not agent_result.timed_out:
                    prepared_instance.sync_repo_to_task(log_dir)
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
    runner_python: str | None = typer.Option(None, "--runner-python", help="Python executable for the external Villani runtime."),
    runner_command_prefix: str | None = typer.Option(None, "--runner-command-prefix", help="Full external Villani command prefix, for example 'python -m villani_code.cli'."),
    runner_cwd: Path | None = typer.Option(None, "--runner-cwd", help="Optional cwd for the external Villani runtime process."),
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
            runner_python=runner_python,
            runner_command_prefix=runner_command_prefix,
            runner_cwd=runner_cwd,
        ),
    )
    run_benchmark(config)


if __name__ == "__main__":
    app()
