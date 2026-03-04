from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from villani_code.anthropic_client import AnthropicClient
from villani_code.checkpoints import CheckpointManager
from villani_code.hooks import HookRunner
from villani_code.mcp import load_mcp_config
from villani_code.permissions import Decision, PermissionConfig, PermissionEngine
from villani_code.edits import ProposalStore
from villani_code.prompting import build_initial_messages, build_system_blocks
from villani_code.skills import discover_skills
from villani_code.streaming import StreamCoalescer, assemble_anthropic_stream
from villani_code.live_display import apply_live_display_delta
from villani_code.tools import execute_tool, tool_specs
from villani_code.transcripts import save_transcript
from villani_code.utils import ensure_dir, is_effectively_empty_content, merge_extra_json, normalize_content_blocks, now_stamp


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
        approval_callback: Callable[[str, dict[str, Any]], bool] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
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
        self.approval_callback = approval_callback or (lambda _n, _i: True)
        self.event_callback = event_callback or (lambda _event: None)
        self.console = Console()
        self._live_stream_buffer = ""
        self._live_stream_started = False
        self.permissions = PermissionEngine(
            PermissionConfig.from_strings(
                deny=["Read(.env)", "Read(secrets/**)", "Bash(curl *)", "Bash(wget *)"],
                ask=[],
                allow=["Read(*)", "Ls(*)", "Grep(*)", "Search(*)", "Glob(*)", "BashSafe(*)", "Write(*)", "Patch(*)", "GitStatus(*)", "GitDiff(*)", "GitLog(*)", "GitBranch(*)", "GitCheckout(*)", "GitCommit(*)"],
            ),
            repo=self.repo,
        )
        self.hooks = HookRunner(hooks={})
        self.checkpoints = CheckpointManager(self.repo)
        self.skills = discover_skills(self.repo)
        self.mcp = load_mcp_config(self.repo)
        self.proposals = ProposalStore(self.repo / ".villani_code" / "edits")
        self.capture_next_diff_proposal = False
        self._coalescer = StreamCoalescer()
        self._no_progress_cycles = 0
        self._recovery_count = 0
        self._last_failed_tool_sig = ""

    def run(self, instruction: str, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        messages = messages or build_initial_messages(self.repo, instruction)
        system = build_system_blocks(self.repo)
        tools = tool_specs()
        transcript: dict[str, Any] = {
            "requests": [],
            "responses": [],
            "tool_invocations": [],
            "tool_results": [],
            "streamed_events_count": 0,
        }
        self._save_session_snapshot(messages)
        empty_turn_retries = 0

        while True:
            self._live_stream_buffer = ""
            self._live_stream_started = False
            self._coalescer = StreamCoalescer()
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
            self.event_callback({"type": "model_request_started", "model": self.model})

            raw = self.client.create_message(payload, stream=self.stream)
            if self.stream:
                events = []
                for event in raw:
                    events.append(event)
                    self._render_stream_event(event)
                transcript["streamed_events_count"] += len(events)
                response = assemble_anthropic_stream(events)
            else:
                response = raw

            response["content"] = normalize_content_blocks(response.get("content"))
            transcript["responses"].append(response)

            assistant_message = {"role": "assistant", "content": response.get("content", [])}
            messages.append(assistant_message)

            tool_uses = [b for b in response.get("content", []) if b.get("type") == "tool_use"]
            empty = is_effectively_empty_content(response.get("content", []))
            if not tool_uses and empty and empty_turn_retries < 2:
                empty_turn_retries += 1
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Continue. You ended your previous turn with no output. Resume the task from where you left off and either call the next tool or provide the next part of the answer.",
                            }
                        ],
                    }
                )
                continue

            if tool_uses or not empty:
                empty_turn_retries = 0

            if not tool_uses:
                if empty:
                    transcript["final_assistant_content"] = response.get("content", [])
                    transcript_path = save_transcript(self.repo, transcript, redact=self.redact)
                    self._save_session_snapshot(messages)
                    return {"response": response, "messages": messages, "transcript_path": str(transcript_path), "transcript": transcript}
                proposal = self._capture_edit_proposal(response)
                if proposal:
                    self.event_callback({"type": "edit_proposed", "proposal_id": proposal.id, "summary": proposal.summary, "files": proposal.files_touched})
                if self._is_no_progress_response(response):
                    self._no_progress_cycles += 1
                    if self._no_progress_cycles < 3:
                        messages.append({"role": "user", "content": [{"type": "text", "text": "No progress detected. Continue with one concrete next step."}]})
                        continue
                else:
                    self._no_progress_cycles = 0
                    self._recovery_count = 0
                if self._no_progress_cycles >= 3:
                    if self._recovery_count >= 2:
                        response = {"role": "assistant", "content": [{"type": "text", "text": "I’m still blocked after two recovery attempts. Which one constraint should I relax first: permissions, test scope, or patch strategy?"}]}
                        transcript["responses"].append(response)
                    else:
                        self._recovery_count += 1
                        self._no_progress_cycles = 0
                        messages.append({"role": "user", "content": [{"type": "text", "text": "RECOVERY MODE: In <=6 lines recap current state, then list next 3 concrete actions, then choose exactly 1 tool call to run next with arguments."}]})
                        continue
                transcript["final_assistant_content"] = response.get("content", [])
                transcript_path = save_transcript(self.repo, transcript, redact=self.redact)
                self._save_session_snapshot(messages)
                return {"response": response, "messages": messages, "transcript_path": str(transcript_path), "transcript": transcript}

            tool_results: list[dict[str, Any]] = []
            for block in tool_uses:
                tool_name = block.get("name", "")
                tool_input = block.get("input", {})
                tool_use_id = str(block.get("id"))
                self.event_callback({"type": "tool_use", "name": tool_name, "input": tool_input, "tool_use_id": tool_use_id})

                hook_pre = self.hooks.run_event("PreToolUse", {"event": "PreToolUse", "tool": tool_name, "input": tool_input})
                if not hook_pre.allow:
                    result = {"content": f"Blocked by hook: {hook_pre.reason}", "is_error": True}
                else:
                    policy = self.permissions.evaluate_with_reason(tool_name, tool_input, bypass=self.bypass_permissions, auto_accept_edits=self.auto_accept_edits)
                    decision = policy.decision
                    self._emit_policy_event(tool_name, tool_input, decision, policy.reason)
                    if decision == Decision.DENY:
                        result = {"content": "Denied by permission policy", "is_error": True}
                    elif decision == Decision.ASK:
                        self.event_callback({"type": "approval_required", "name": tool_name, "input": tool_input})
                        if not self.approval_callback(tool_name, tool_input):
                            result = {"content": "User denied tool execution", "is_error": True}
                        else:
                            result = execute_tool(tool_name, tool_input, self.repo, unsafe=self.unsafe)
                    elif self.plan_mode and tool_name in {"Write", "Patch"}:
                        result = {"content": "Plan mode: edit not executed", "is_error": False}
                    else:
                        if tool_name in {"Write", "Patch"}:
                            file_path = Path(tool_input.get("file_path", ""))
                            self.checkpoints.create([file_path], message_index=len(messages))
                        result = execute_tool(tool_name, tool_input, self.repo, unsafe=self.unsafe)
                self.hooks.run_event("PostToolUse", {"event": "PostToolUse", "tool": tool_name, "input": tool_input, "result": result})

                transcript["tool_invocations"].append({"name": tool_name, "input": tool_input, "id": tool_use_id})
                transcript["tool_results"].append(result)
                self.event_callback({"type": "tool_result", "name": tool_name, "input": tool_input, "tool_use_id": tool_use_id, "is_error": result["is_error"]})
                tool_results.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": result["content"], "is_error": result["is_error"]})

            if tool_results and any(not r.get("is_error") for r in transcript["tool_results"][-len(tool_results):]):
                self._no_progress_cycles = 0
                self._recovery_count = 0
                self._last_failed_tool_sig = ""
            else:
                sig = "|".join(f"{b.get('name')}:{b.get('input')}" for b in tool_uses)
                if sig and sig == self._last_failed_tool_sig:
                    self._no_progress_cycles += 1
                self._last_failed_tool_sig = sig

            messages.append({"role": "user", "content": tool_results})

    def _emit_policy_event(self, tool_name: str, tool_input: dict[str, Any], decision: Decision, reason: str) -> None:
        if tool_name != "Bash":
            return
        command = str(tool_input.get("command", ""))
        cwd = str((self.repo / str(tool_input.get("cwd", "."))).resolve())
        outcome = {Decision.ALLOW: "AUTO_APPROVE", Decision.ASK: "ASK", Decision.DENY: "DENY"}[decision]
        ts = datetime.now(timezone.utc).isoformat()
        line = json.dumps({"timestamp": ts, "cwd": cwd, "command": command, "outcome": outcome, "reason": reason})
        log_dir = self.repo / ".villani_code" / "logs"
        ensure_dir(log_dir)
        with (log_dir / "commands.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self.event_callback({"type": "command_policy", "command": command, "cwd": cwd, "outcome": outcome, "reason": reason})

    def _capture_edit_proposal(self, response: dict[str, Any]):
        text_blocks = [b.get("text", "") for b in response.get("content", []) if b.get("type") == "text"]
        if not text_blocks:
            return None
        merged = "\n".join(text_blocks)
        has_diff = "--- " in merged and "+++ " in merged and "@@" in merged
        if not (self.capture_next_diff_proposal or has_diff):
            return None
        files: list[str] = []
        for ln in merged.splitlines():
            if ln.startswith("+++ "):
                p = ln[4:].strip().split("\t")[0]
                if p.startswith("b/"):
                    p = p[2:]
                files.append(p)
        proposal = self.proposals.create(diff_text=merged, files_touched=files, summary=f"Proposed edit touching {len(files)} file(s)")
        self.capture_next_diff_proposal = False
        return proposal

    def _is_no_progress_response(self, response: dict[str, Any]) -> bool:
        blocks = response.get("content", [])
        text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if not text:
            return True
        return len(text) <= 2

    def _save_session_snapshot(self, messages: list[dict[str, Any]]) -> None:
        root = self.repo / ".villani_code" / "sessions"
        ensure_dir(root)
        sid = "last"
        (root / f"{sid}.json").write_text(json.dumps({"id": sid, "messages": messages, "cwd": str(self.repo), "settings": {"model": self.model}}, indent=2), encoding="utf-8")

    def _render_stream_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "message_stop":
            tail = self._coalescer.flush()
            if tail:
                print(tail, end="", flush=True)
            return
        if event.get("type") != "content_block_delta":
            return
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            raw_text = delta.get("text", "")
            before = self._live_stream_buffer
            self._live_stream_buffer, updated_started = apply_live_display_delta(before, raw_text, self._live_stream_started)
            if updated_started and not self._live_stream_started:
                self.event_callback({"type": "first_text_delta"})
            self._live_stream_started = updated_started
            appended = self._live_stream_buffer[len(before) :]
            if appended:
                emit = self._coalescer.consume(appended)
                if emit:
                    print(emit, end="", flush=True)
        if self.verbose and delta.get("type") == "input_json_delta":
            self.console.print(f"[dim]tool delta: {delta.get('partial_json','')[:200]}[/dim]")
