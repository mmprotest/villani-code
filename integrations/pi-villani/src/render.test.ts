import assert from "node:assert/strict";
import test from "node:test";
import { BridgeEvent } from "./protocol.js";
import { renderFinalSummary } from "./render.js";

function render(event: Extract<BridgeEvent, { type: "run_completed" }>): string {
  const messages: string[] = [];
  renderFinalSummary(event, { markdown: (message) => messages.push(message) });
  return messages.join("\n");
}

function completed(verification_passed?: boolean | null): Extract<BridgeEvent, { type: "run_completed" }> {
  return {
    type: "run_completed",
    id: "run-1",
    success: true,
    changed_files: [],
    preexisting_dirty_files: [],
    verification_passed: verification_passed as boolean | null,
    summary: "done",
    transcript_path: null,
  };
}

test("final summary renders passed verification", () => {
  const output = render(completed(true));
  assert.match(output, /\*\*Verification:\*\* passed/);
  assert.doesNotMatch(output, /Verification passed:/);
});

test("final summary renders failed verification", () => {
  const output = render(completed(false));
  assert.match(output, /\*\*Verification:\*\* failed/);
  assert.doesNotMatch(output, /Verification passed:/);
});

test("final summary renders null verification as not reported", () => {
  const output = render(completed(null));
  assert.match(output, /\*\*Verification:\*\* not reported/);
  assert.doesNotMatch(output, /Verification passed: null/);
});

test("final summary renders missing verification as not reported", () => {
  const event = completed(true) as Partial<Extract<BridgeEvent, { type: "run_completed" }>> as Extract<BridgeEvent, { type: "run_completed" }>;
  delete (event as { verification_passed?: boolean | null }).verification_passed;
  const output = render(event);
  assert.match(output, /\*\*Verification:\*\* not reported/);
  assert.doesNotMatch(output, /Verification passed: undefined/);
});
