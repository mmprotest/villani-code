from __future__ import annotations

from pathlib import Path
from typing import Any

from villani_code.anthropic_client import AnthropicClient
from villani_code.prompting import build_initial_messages, build_system_blocks
from villani_code.streaming import assemble_anthropic_stream
from villani_code.tools import execute_tool, tool_specs
from villani_code.transcripts import save_transcript
from villani_code.utils import merge_extra_json, normalize_content_blocks


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

    def run(self, instruction: str) -> dict[str, Any]:
        messages = build_initial_messages(self.repo, instruction)
        system = build_system_blocks()
        tools = tool_specs()
        transcript: dict[str, Any] = {
            "requests": [],
            "responses": [],
            "tool_invocations": [],
            "tool_results": [],
            "streamed_events_count": 0,
        }

        while True:
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

            raw = self.client.create_message(payload, stream=self.stream)
            if self.stream:
                events = list(raw)
                transcript["streamed_events_count"] += len(events)
                response = assemble_anthropic_stream(events)
            else:
                response = raw

            response["content"] = normalize_content_blocks(response.get("content"))
            transcript["responses"].append(response)

            assistant_message = {
                "role": "assistant",
                "content": response.get("content", []),
            }
            messages.append(assistant_message)

            tool_uses = [b for b in response.get("content", []) if b.get("type") == "tool_use"]
            if not tool_uses:
                transcript["final_assistant_content"] = response.get("content", [])
                transcript_path = save_transcript(self.repo, transcript, redact=self.redact)
                return {
                    "response": response,
                    "messages": messages,
                    "transcript_path": str(transcript_path),
                    "transcript": transcript,
                }

            for block in tool_uses:
                tool_name = block.get("name", "")
                tool_input = block.get("input", {})
                tool_use_id = str(block.get("id"))
                result = execute_tool(tool_name, tool_input, self.repo, unsafe=self.unsafe)
                transcript["tool_invocations"].append({"name": tool_name, "input": tool_input, "id": tool_use_id})
                transcript["tool_results"].append(result)
                tool_result_message = {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": result["content"],
                            "is_error": result["is_error"],
                        }
                    ],
                }
                messages.append(tool_result_message)
