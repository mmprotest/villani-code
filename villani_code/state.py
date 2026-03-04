from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from villani_code.anthropic_client import AnthropicClient
from villani_code.checkpoints import CheckpointManager
from villani_code.hooks import HookRunner
from villani_code.mcp import load_mcp_config
from villani_code.permissions import Decision, PermissionConfig, PermissionEngine
from villani_code.prompting import build_initial_messages, build_system_blocks
from villani_code.skills import discover_skills
from villani_code.streaming import assemble_anthropic_stream
from villani_code.tools import execute_tool, tool_specs
from villani_code.transcripts import save_transcript
from villani_code.turn_validation import repair_messages, validate_tool_turns
from villani_code.utils import ensure_dir, merge_extra_json, normalize_content_blocks, now_stamp


class Runner:
    def __init__(
        self,
        client: AnthropicClient,
        repo: Path,
        model: str,
        max_tokens: int = 4096,
        stream: bool = True,
        thinking: Any = None,
        unsafe: bool = False,
        verbose: bool = False,
        extra_json: str | None = None,
        redact: bool = False,
        bypass_permissions: bool = False,
        auto_accept_edits: bool = False,
        plan_mode: bool = False,
        approval_callback: Callable[[str, dict[str, Any], str | None], str] | None = None,
        stream_event_handler: Callable[[dict[str, Any]], None] | None = None,
        tool_event_handler: Callable[[str, dict[str, Any]], None] | None = None,
        interactive_mode: bool = False,
    ):
        self.client = client
        self.repo = repo
        self.model = model
        self.max_tokens = max_tokens
        self.stream = stream
        self.thinking = thinking
        self.unsafe = unsafe
        self.verbose = verbose
        self.extra_json = extra_json
        self.redact = redact
        self.bypass_permissions = bypass_permissions
        self.auto_accept_edits = auto_accept_edits
        self.plan_mode = plan_mode
        self.approval_callback = approval_callback or (lambda _n, _i, _id=None: "allow_once")
        self.stream_event_handler = stream_event_handler
        self.tool_event_handler = tool_event_handler
        self.interactive_mode = interactive_mode
        self.console = Console()
        self.permissions = PermissionEngine(
            PermissionConfig.from_strings(
                deny=["Read(.env)", "Read(secrets/**)", "Bash(curl *)", "Bash(wget *)"],
                ask=[],
                allow=["Read(*)", "Ls(*)", "Grep(*)", "Search(*)", "Glob(*)", "Bash(*)", "Write(*)", "Patch(*)", "GitStatus(*)", "GitDiff(*)", "GitLog(*)", "GitBranch(*)", "GitCheckout(*)", "GitCommit(*)"],
            ),
            repo=self.repo,
        )
        self.hooks = HookRunner(hooks={})
        self.checkpoints = CheckpointManager(self.repo)
        self.skills = discover_skills(self.repo)
        self.mcp = load_mcp_config(self.repo)

    def run(self, instruction: str, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        messages = messages or build_initial_messages(self.repo, instruction)
        messages = repair_messages(messages)
        system = build_system_blocks(self.repo)
        tools = tool_specs()
        pending_tool_use_ids: set[str] = set()
        transcript: dict[str, Any] = {
            "requests": [],
            "responses": [],
            "tool_invocations": [],
            "tool_results": [],
            "streamed_events_count": 0,
        }
        self._save_session_snapshot(messages)

        while True:
            messages = repair_messages(messages)
            try:
                validate_tool_turns(messages)
            except ValueError as exc:
                error_text = f"Invalid conversation state before model call: {exc}"
                if self.stream_event_handler:
                    self.stream_event_handler({"type": "error", "error": error_text})
                transcript["error"] = error_text
                transcript_path = save_transcript(self.repo, transcript, redact=self.redact)
                self._save_session_snapshot(messages)
                return {"error": error_text, "messages": messages, "transcript_path": str(transcript_path), "transcript": transcript}

            if pending_tool_use_ids:
                pending = ", ".join(sorted(pending_tool_use_ids))
                error_text = f"Pending tool_use ids not resolved before model call: {pending}"
                if self.stream_event_handler:
                    self.stream_event_handler({"type": "error", "error": error_text})
                transcript["error"] = error_text
                transcript_path = save_transcript(self.repo, transcript, redact=self.redact)
                self._save_session_snapshot(messages)
                return {"error": error_text, "messages": messages, "transcript_path": str(transcript_path), "transcript": transcript}

            payload = {
                "model": self.model,
                "messages": messages,
                "system": system,
                "tools": tools,
                "max_tokens": self.max_tokens,
                "stream": self.stream,
            }
            if self.thinking is not None:
                payload["thinking"] = self.thinking
            payload = merge_extra_json(payload, self.extra_json)
            transcript["requests"].append(payload)
            if self.stream_event_handler:
                self.stream_event_handler({"type": "model_start", "model": self.model})

            raw = self.client.create_message(payload, stream=self.stream)
            if self.stream:
                events = []
                for event in raw:
                    events.append(event)
                    self._render_stream_event(event)
                    if self.stream_event_handler:
                        self.stream_event_handler(event)
                transcript["streamed_events_count"] += len(events)
                response = assemble_anthropic_stream(events)
            else:
                response = raw

            response["content"] = normalize_content_blocks(response.get("content"))
            if self.stream_event_handler:
                self.stream_event_handler(
                    {
                        "type": "model_stop",
                        "usage": response.get("usage", {}),
                        "stop_reason": response.get("stop_reason"),
                    }
                )
            transcript["responses"].append(response)

            assistant_message = {"role": "assistant", "content": response.get("content", [])}
            messages.append(assistant_message)
            messages = repair_messages(messages)

            merged_assistant_content = messages[-1].get("content", []) if messages else []
            tool_uses = [b for b in merged_assistant_content if b.get("type") == "tool_use"]
            if not tool_uses:
                transcript["final_assistant_content"] = response.get("content", [])
                transcript_path = save_transcript(self.repo, transcript, redact=self.redact)
                self._save_session_snapshot(messages)
                return {"response": response, "messages": messages, "transcript_path": str(transcript_path), "transcript": transcript}

            pending_tool_use_ids = {str(block.get("id")) for block in tool_uses}
            tool_result_blocks: list[dict[str, Any]] = []
            for block in tool_uses:
                tool_name = block.get("name", "")
                if self.tool_event_handler:
                    self.tool_event_handler("tool_use", block)
                tool_input = block.get("input", {})
                tool_use_id = str(block.get("id"))

                hook_pre = self.hooks.run_event("PreToolUse", {"event": "PreToolUse", "tool": tool_name, "input": tool_input})
                if not hook_pre.allow:
                    result = {"content": f"Blocked by hook: {hook_pre.reason}", "is_error": True}
                else:
                    decision = self.permissions.evaluate(tool_name, tool_input, bypass=self.bypass_permissions, auto_accept_edits=self.auto_accept_edits)
                    if decision == Decision.DENY:
                        result = {"content": "Denied by permission policy", "is_error": True}
                    elif self.plan_mode and tool_name in {"Write", "Patch"}:
                        result = {"content": "Plan mode: edit not executed", "is_error": False}
                    elif decision == Decision.ASK:
                        approval_decision = self.approval_callback(tool_name, tool_input, tool_use_id)
                        if approval_decision == "deny":
                            result = {"content": "User denied tool execution", "is_error": True}
                        else:
                            if self.tool_event_handler:
                                self.tool_event_handler("tool_start", {"name": tool_name, "input": tool_input, "id": tool_use_id})
                            if tool_name in {"Write", "Patch"}:
                                file_path = Path(tool_input.get("file_path", ""))
                                self.checkpoints.create([file_path], message_index=len(messages))
                            result = execute_tool(tool_name, tool_input, self.repo, unsafe=self.unsafe)
                    else:
                        if self.tool_event_handler:
                            self.tool_event_handler("tool_start", {"name": tool_name, "input": tool_input, "id": tool_use_id})
                        if tool_name in {"Write", "Patch"}:
                            file_path = Path(tool_input.get("file_path", ""))
                            self.checkpoints.create([file_path], message_index=len(messages))
                        result = execute_tool(tool_name, tool_input, self.repo, unsafe=self.unsafe)
                self.hooks.run_event("PostToolUse", {"event": "PostToolUse", "tool": tool_name, "input": tool_input, "result": result})

                transcript["tool_invocations"].append({"name": tool_name, "input": tool_input, "id": tool_use_id})
                if self.tool_event_handler:
                    preview = str(result.get("content", ""))[:240]
                    self.tool_event_handler("tool_end", {"name": tool_name, "result": result, "id": tool_use_id, "is_error": bool(result.get("is_error")), "preview": preview})
                    self.tool_event_handler("tool_result", {"name": tool_name, "result": result, "id": tool_use_id})
                transcript["tool_results"].append(result)
                tool_result_blocks.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": result["content"], "is_error": result["is_error"]})

            messages.append({"role": "user", "content": tool_result_blocks})
            pending_tool_use_ids -= {str(block.get("tool_use_id")) for block in tool_result_blocks}

    def _save_session_snapshot(self, messages: list[dict[str, Any]]) -> None:
        root = self.repo / ".villani_code" / "sessions"
        ensure_dir(root)
        sid = "last"
        (root / f"{sid}.json").write_text(json.dumps({"id": sid, "messages": messages, "cwd": str(self.repo), "settings": {"model": self.model}}, indent=2), encoding="utf-8")

    def _render_stream_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "content_block_delta":
            return
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta" and not self.interactive_mode:
            print(delta.get("text", ""), end="", flush=True)
        if self.verbose and delta.get("type") == "input_json_delta":
            self.console.print(f"[dim]tool delta: {delta.get('partial_json','')[:200]}[/dim]")
