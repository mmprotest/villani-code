from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from villani_code.autonomous import VillaniModeConfig, VillaniModeController
from villani_code.checkpoints import CheckpointManager
from villani_code.context_budget import ContextBudget
from villani_code.edits import ProposalStore
from villani_code.hooks import HookRunner
from villani_code.indexing import DEFAULT_IGNORE, RepoIndex
from villani_code.live_display import apply_live_display_delta
from villani_code.mcp import load_mcp_config
from villani_code.permissions import Decision, PermissionConfig, PermissionEngine
from villani_code.prompting import build_initial_messages, build_system_blocks
from villani_code.llm_client import LLMClient
from villani_code.repo_map import build_repo_map
from villani_code.retrieval import Retriever
from villani_code.skills import discover_skills
from villani_code.streaming import StreamCoalescer, assemble_anthropic_stream
from villani_code.tools import execute_tool, tool_specs
from villani_code.transcripts import save_transcript
from villani_code.utils import ensure_dir, is_effectively_empty_content, merge_extra_json, normalize_content_blocks


class Runner:
    def __init__(
        self,
        client: LLMClient,
        repo: Path,
        model: str,
        max_tokens: int = 4096,
        stream: bool = True,
        print_stream: bool = True,
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
        small_model: bool = False,
        villani_mode: bool = False,
        villani_objective: str | None = None,
    ):
        self.client = client
        self.repo = repo
        self.model = model
        self.max_tokens = max_tokens
        self.stream = stream
        self.print_stream = print_stream
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
        self.small_model = small_model
        self.villani_mode = villani_mode
        self.villani_objective = villani_objective
        self.villani_config = VillaniModeConfig(enabled=villani_mode, steering_objective=villani_objective)
        self.console = Console()
        self.permissions = PermissionEngine(
            PermissionConfig.from_strings(
                deny=["Read(.env)", "Read(secrets/**)", "Bash(curl *)", "Bash(wget *)"],
                ask=["Write(*)", "Patch(*)"],
                allow=["Read(*)", "Ls(*)", "Grep(*)", "Search(*)", "Glob(*)", "BashSafe(*)", "GitStatus(*)", "GitDiff(*)", "GitLog(*)", "GitBranch(*)", "GitCheckout(*)", "GitCommit(*)"],
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
        self._live_stream_buffer = ""
        self._live_stream_started = False
        self._no_progress_cycles = 0
        self._recovery_count = 0
        self._last_failed_tool_sig = ""
        self._repo_map = ""
        self._retriever: Retriever | None = None
        self._context_budget = ContextBudget(max_chars=35000, keep_last_turns=4) if self.small_model else None
        self._files_read: set[str] = set()
        self._pending_verification = ""
        if self.small_model:
            self._init_small_model_support()


    def run_villani_mode(self) -> dict[str, Any]:
        controller = VillaniModeController(self, self.repo, steering_objective=self.villani_objective, event_callback=self.event_callback)
        summary = controller.run()
        text = VillaniModeController.format_summary(summary)
        response = {"role": "assistant", "content": [{"type": "text", "text": text}]}
        return {"response": response, "summary": summary}

    def run(self, instruction: str, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        messages = messages or build_initial_messages(self.repo, instruction)
        system = build_system_blocks(self.repo, repo_map=self._repo_map if self.small_model else "", villani_mode=self.villani_mode)
        tools = tool_specs()
        transcript: dict[str, Any] = {"requests": [], "responses": [], "tool_invocations": [], "tool_results": [], "streamed_events_count": 0}
        self._save_session_snapshot(messages)
        empty_turn_retries = 0

        while True:
            self._live_stream_buffer = ""
            self._live_stream_started = False
            self._coalescer = StreamCoalescer()
            turn_messages = self._prepare_messages_for_model(messages)
            payload = {"model": self.model, "messages": turn_messages, "system": system, "tools": tools, "max_tokens": self.max_tokens, "stream": self.stream}
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
            messages.append({"role": "assistant", "content": response.get("content", [])})

            tool_uses = [b for b in response.get("content", []) if b.get("type") == "tool_use"]
            empty = is_effectively_empty_content(response.get("content", []))
            if not tool_uses and empty and empty_turn_retries < 2:
                empty_turn_retries += 1
                messages.append({"role": "user", "content": [{"type": "text", "text": "Continue. You ended your previous turn with no output. Resume the task from where you left off and either call the next tool or provide the next part of the answer."}]})
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
                tool_input = dict(block.get("input", {}))
                tool_use_id = str(block.get("id"))
                self.event_callback({"type": "tool_use", "name": tool_name, "input": tool_input, "tool_use_id": tool_use_id})

                result = self._execute_tool_with_policy(tool_name, tool_input, tool_use_id, len(messages))
                if self.small_model:
                    result = self._truncate_tool_result(tool_name, result)
                    if tool_name == "Read" and not result.get("is_error"):
                        self._files_read.add(str(tool_input.get("file_path", "")))
                    if tool_name in {"Write", "Patch"} and not result.get("is_error"):
                        self._pending_verification = self._run_verification()

                self.hooks.run_event("PostToolUse", {"event": "PostToolUse", "tool": tool_name, "input": tool_input, "result": result})
                self.event_callback({"type": "tool_finished", "name": tool_name, "input": tool_input, "tool_use_id": tool_use_id, "is_error": result["is_error"]})
                transcript["tool_invocations"].append({"name": tool_name, "input": tool_input, "id": tool_use_id})
                transcript["tool_results"].append(result)
                self.event_callback({"type": "tool_result", "name": tool_name, "input": tool_input, "tool_use_id": tool_use_id, "is_error": result["is_error"]})
                tool_results.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": result["content"], "is_error": result["is_error"]})

            if self._pending_verification:
                tool_results.append({"type": "text", "text": self._pending_verification})
                self._pending_verification = ""

            if tool_results and any(not r.get("is_error") for r in transcript["tool_results"][-len(tool_uses) :]):
                self._no_progress_cycles = 0
                self._recovery_count = 0
                self._last_failed_tool_sig = ""
            else:
                sig = "|".join(f"{b.get('name')}:{b.get('input')}" for b in tool_uses)
                if sig and sig == self._last_failed_tool_sig:
                    self._no_progress_cycles += 1
                self._last_failed_tool_sig = sig
            messages.append({"role": "user", "content": tool_results})

    def _execute_tool_with_policy(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str, message_count: int) -> dict[str, Any]:
        hook_pre = self.hooks.run_event("PreToolUse", {"event": "PreToolUse", "tool": tool_name, "input": tool_input})
        if not hook_pre.allow:
            return {"content": f"Blocked by hook: {hook_pre.reason}", "is_error": True}

        if self.small_model:
            policy_error = self._small_model_tool_guard(tool_name, tool_input)
            if policy_error:
                return {"content": policy_error, "is_error": True}
            self._tighten_tool_input(tool_name, tool_input)

        policy = self.permissions.evaluate_with_reason(tool_name, tool_input, bypass=self.bypass_permissions, auto_accept_edits=self.auto_accept_edits)
        self._emit_policy_event(tool_name, tool_input, policy.decision, policy.reason)
        if policy.decision == Decision.DENY:
            return {"content": "Denied by permission policy", "is_error": True}
        if policy.decision == Decision.ASK:
            if self.villani_mode:
                self.event_callback({"type": "approval_auto_resolved", "name": tool_name, "input": tool_input})
            else:
                self.event_callback({"type": "approval_required", "name": tool_name, "input": tool_input})
                if not self.approval_callback(tool_name, tool_input):
                    return {"content": "User denied tool execution", "is_error": True}
        elif self.plan_mode and tool_name in {"Write", "Patch"}:
            return {"content": "Plan mode: edit not executed", "is_error": False}

        if tool_name in {"Write", "Patch"}:
            self.checkpoints.create([Path(tool_input.get("file_path", ""))], message_index=message_count)
        self.event_callback({"type": "tool_started", "name": tool_name, "input": tool_input, "tool_use_id": tool_use_id})
        return execute_tool(tool_name, tool_input, self.repo, unsafe=self.unsafe)

    def _prepare_messages_for_model(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = [dict(m) for m in messages]
        if self.small_model:
            self._inject_retrieval_briefing(prepared)
            if self._context_budget:
                prepared = self._context_budget.compact(prepared)
        return prepared

    def _inject_retrieval_briefing(self, messages: list[dict[str, Any]]) -> None:
        if not self._retriever or not messages:
            return
        last = messages[-1]
        if last.get("role") != "user":
            return
        content = last.get("content", [])
        if not isinstance(content, list):
            return
        user_text = "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
        if not user_text or "<retrieval-briefing>" in user_text:
            return
        hits = self._retriever.query(user_text, k=8)
        if not hits:
            return
        briefing = "\n".join(f"- {h.path}: {h.reason}" for h in hits)
        content.insert(0, {"type": "text", "text": f"<retrieval-briefing>\n{briefing}\n</retrieval-briefing>"})

    def _init_small_model_support(self) -> None:
        index_path = self.repo / ".villani_code" / "index" / "index.json"
        if index_path.exists():
            idx = RepoIndex.load(index_path)
            if idx.needs_rebuild(self.repo):
                idx = RepoIndex.build(self.repo, DEFAULT_IGNORE)
                idx.save(index_path)
                self.event_callback({"type": "index_built", "path": str(index_path)})
            else:
                self.event_callback({"type": "index_loaded", "path": str(index_path)})
        else:
            idx = RepoIndex.build(self.repo, DEFAULT_IGNORE)
            idx.save(index_path)
            self.event_callback({"type": "index_built", "path": str(index_path)})
        self._retriever = Retriever(idx)
        self._repo_map = build_repo_map(idx)

    def _small_model_tool_guard(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        if tool_name in {"Write", "Patch"}:
            fp = str(tool_input.get("file_path", ""))
            if fp and fp not in self._files_read:
                read_result = execute_tool("Read", {"file_path": fp, "max_bytes": 8000}, self.repo, unsafe=self.unsafe)
                if read_result.get("is_error"):
                    return f"Read-before-edit policy: failed to auto-read {fp}. Read it explicitly before editing."
                self._files_read.add(fp)
        if tool_name == "Write":
            file_path = str(tool_input.get("file_path", ""))
            path = (self.repo / file_path).resolve()
            if path.exists() and path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                if len(text) > 10_000 or len(text.splitlines()) > 200:
                    return "Small-model mode policy: avoid whole-file writes for large files; use Patch instead."
        return None

    def _tighten_tool_input(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        if tool_name == "Read":
            tool_input["max_bytes"] = min(int(tool_input.get("max_bytes", 200000)), 50_000)
        if tool_name == "Grep":
            tool_input["max_results"] = min(int(tool_input.get("max_results", 200)), 60)

    def _truncate_tool_result(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("is_error"):
            return result
        content = str(result.get("content", ""))
        if tool_name == "Bash" and len(content) > 6000:
            result["content"] = content[:2000] + "\n...\n" + content[-3000:]
        elif len(content) > 50000:
            result["content"] = content[:50000]
        return result

    def _run_verification(self) -> str:
        commands = [["git", "diff", "--stat"], ["git", "diff", "--", "."]]
        if (self.repo / "pyproject.toml").exists() or (self.repo / "requirements.txt").exists():
            commands.append(["python", "-m", "compileall", "-q", str(self.repo)])
            if (self.repo / "tests").exists():
                commands.append(["pytest", "-q", "tests/test_runner_defaults.py"])
        lines = ["<verification>"]
        for cmd in commands:
            proc = subprocess.run(cmd, cwd=self.repo, capture_output=True, text=True)
            stderr_lines = "\n".join([ln for ln in proc.stderr.splitlines() if ln][:5])
            stdout = proc.stdout[:1500]
            lines.append(f"command: {' '.join(cmd)}")
            lines.append(f"exit: {proc.returncode}")
            if stdout:
                lines.append(f"stdout:\n{stdout}")
            if stderr_lines:
                lines.append(f"key stderr:\n{stderr_lines}")
        lines.append("</verification>")
        self.event_callback({"type": "verification_ran"})
        return "\n".join(lines)

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
        (root / "last.json").write_text(json.dumps({"id": "last", "messages": messages, "cwd": str(self.repo), "settings": {"model": self.model}}, indent=2), encoding="utf-8")

    def _render_stream_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "message_stop":
            tail = self._coalescer.flush()
            if tail:
                if self.print_stream:
                    print(tail, end="", flush=True)
                else:
                    self.event_callback({"type": "stream_text", "text": tail})
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
                    if self.print_stream:
                        print(emit, end="", flush=True)
                    else:
                        self.event_callback({"type": "stream_text", "text": emit})
        if self.verbose and delta.get("type") == "input_json_delta":
            partial = f"[dim]tool delta: {delta.get('partial_json','')[:200]}[/dim]"
            if self.print_stream:
                self.console.print(partial)
            else:
                self.event_callback({"type": "stream_text", "text": partial})
