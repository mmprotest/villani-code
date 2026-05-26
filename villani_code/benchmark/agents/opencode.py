from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from villani_code.benchmark.agents.base import AgentRunner


class OpenCodeAgentRunner(AgentRunner):
    name = "opencode"
    _LOCAL_PROVIDER_ID = "villani-openai-compatible"
    _LOCAL_MODEL_ALIAS = "benchmark-model"
    _TASK_INSTRUCTION = (
        "Complete the benchmark task described in the attached file. "
        "Modify the current repository. Do not ask for clarification. Stop when done."
    )

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        return normalized

    def build_command(
        self,
        repo_path: Path,
        prompt: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
        benchmark_config_json: str | None = None,
    ) -> list[str]:
        if not model:
            raise ValueError("opencode requires --model for fair same-model benchmarking")
        prompt_temp_dir = Path(tempfile.mkdtemp(prefix="villani_opencode_"))
        self._prompt_temp_dir = prompt_temp_dir
        prompt_file = prompt_temp_dir / "villani_opencode_benchmark_prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        model_arg = f"{self._LOCAL_PROVIDER_ID}/{self._LOCAL_MODEL_ALIAS}" if base_url else model
        return [
            "opencode",
            "run",
            self._TASK_INSTRUCTION,
            "--model",
            model_arg,
            "--format",
            "json",
            "--dangerously-skip-permissions",
            "--file",
            str(prompt_file.resolve()),
        ]

    def build_env(self, *, base_url: str | None, api_key: str | None) -> dict[str, str]:
        env = super().build_env(base_url=base_url, api_key=api_key)
        if api_key:
            env["OPENAI_API_KEY"] = api_key
        elif base_url:
            env["OPENAI_API_KEY"] = "dummy"
        return env

    def run_agent(
        self,
        repo_path: Path,
        prompt: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
        timeout: int,
        benchmark_config_json: str | None = None,
        debug_dir: Path | None = None,
    ):
        generated_config: Path | None = None
        self._prompt_temp_dir: Path | None = None
        if base_url:
            if not model:
                raise ValueError("opencode requires --model for fair same-model benchmarking")
            config_path = repo_path / "opencode.json"
            if config_path.exists():
                raise RuntimeError(
                    f"opencode benchmark adapter cannot safely overwrite existing config: {config_path}"
                )
            config_path.write_text(
                json.dumps(
                    {
                        "$schema": "https://opencode.ai/config.json",
                        "provider": {
                            self._LOCAL_PROVIDER_ID: {
                                "npm": "@ai-sdk/openai-compatible",
                                "name": "Villani benchmark OpenAI-compatible",
                                "options": {
                                    "baseURL": self._normalize_base_url(base_url),
                                    "apiKey": "{env:OPENAI_API_KEY}",
                                },
                                "models": {self._LOCAL_MODEL_ALIAS: {"name": model}},
                            }
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            generated_config = config_path
        try:
            return super().run_agent(
                repo_path,
                prompt,
                model,
                base_url,
                api_key,
                provider,
                timeout,
                benchmark_config_json=benchmark_config_json,
                debug_dir=debug_dir,
            )
        finally:
            if self._prompt_temp_dir is not None and self._prompt_temp_dir.exists():
                shutil.rmtree(self._prompt_temp_dir, ignore_errors=True)
            if generated_config is not None and generated_config.exists():
                generated_config.unlink()
