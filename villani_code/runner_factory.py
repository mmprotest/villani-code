from __future__ import annotations

import json, os
from pathlib import Path
from typing import Any, Callable, Literal
import typer
from villani_code.anthropic_client import AnthropicClient
from villani_code.openai_client import OpenAIClient
from villani_code.runtime_safety import ensure_runtime_dependencies_not_shadowed
from villani_code.state import Runner
from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.debug_mode import DebugConfig, DebugMode, build_debug_config


def build_runner(*, base_url: str, model: str, repo: Path, max_tokens: int = 4096, stream: bool = True, thinking: Any = None, unsafe: bool = False, verbose: bool = False, extra_json: str | None = None, redact: bool = False, dangerously_skip_permissions: bool = False, auto_accept_edits: bool = False, auto_approve: bool = False, plan_mode: Literal['off','auto','strict'] = 'auto', max_repair_attempts: int = 2, small_model: bool = False, provider: Literal['anthropic','openai'] = 'anthropic', api_key: str | None = None, villani_mode: bool = False, villani_objective: str | None = None, event_callback: Callable[[dict[str, Any]], None] | None = None, approval_callback: Callable[[str, dict[str, Any]], bool] | None = None, external_approval_mode: bool = False, benchmark_runtime_json: str | None = None, debug_mode: DebugMode = DebugMode.OFF, debug_dir: Path | None = None, memory_enabled: bool = False, memory_update_interval_tool_calls: int = 5) -> Runner:
    resolved_repo = repo.resolve()
    try:
        ensure_runtime_dependencies_not_shadowed(resolved_repo)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if provider == 'openai':
        client = OpenAIClient(base_url=base_url, api_key=api_key or os.environ.get('OPENAI_API_KEY'))
    else:
        _ = api_key or os.environ.get('ANTHROPIC_API_KEY')
        client = AnthropicClient(base_url=base_url)
    thinking_obj = None
    if thinking:
        if isinstance(thinking, str):
            try: thinking_obj = json.loads(thinking)
            except json.JSONDecodeError: thinking_obj = thinking
        else: thinking_obj = thinking
    benchmark_config = BenchmarkRuntimeConfig.model_validate_json(benchmark_runtime_json) if benchmark_runtime_json else None
    debug_config = build_debug_config(debug_mode.value if isinstance(debug_mode, DebugMode) else str(debug_mode), debug_dir=debug_dir) if not isinstance(debug_mode, DebugConfig) else debug_mode
    return Runner(client=client, repo=resolved_repo, model=model, max_tokens=max_tokens, stream=stream, thinking=thinking_obj, unsafe=unsafe, verbose=verbose, extra_json=extra_json, redact=redact, bypass_permissions=dangerously_skip_permissions, auto_accept_edits=auto_accept_edits, auto_approve=auto_approve, plan_mode=plan_mode, max_repair_attempts=max_repair_attempts, approval_callback=approval_callback, event_callback=event_callback, small_model=small_model, villani_mode=villani_mode, villani_objective=villani_objective, benchmark_config=benchmark_config, debug_config=debug_config, provider=provider, memory_enabled=memory_enabled, memory_update_interval_tool_calls=memory_update_interval_tool_calls, external_approval_mode=external_approval_mode)
