import test from 'node:test';
import assert from 'node:assert/strict';
import {
  cleanAssistantText,
  reduceVillaniUiState,
  renderBridgeEvent,
  resetVillaniCopyCounters,
  resetVillaniUiState,
  toolStartedMessage,
  villaniCopy,
} from './render.js';
import { approvalMessage, approvalTitle } from './index.js';

function ctxRecorder() {
  const statuses: string[] = [];
  const widgets: any[] = [];
  const notifications: string[] = [];
  return {
    statuses,
    widgets,
    notifications,
    ctx: { ui: {
      setStatus: async (_key: string, value: string) => { statuses.push(value); },
      setWidget: async (_key: string, value: any) => { widgets.push(value); },
      notify: async (message: string) => { notifications.push(message); },
    } },
  };
}

test('toolStartedMessage for Read with path does not say unknown', () => {
  const msg = toolStartedMessage({ type: 'tool_started', tool: 'Read', path: 'src/foo.py' });
  assert.match(msg, /File: src\/foo.py/);
  assert.doesNotMatch(msg, /unknown/i);
});

test('toolStartedMessage for Read without path does not say unknown', () => {
  const msg = toolStartedMessage({ type: 'tool_started', tool: 'Read' });
  assert.equal(msg, 'Villani reads file. File nervous.');
  assert.doesNotMatch(msg, /unknown/i);
});

test('Read event sets reading Villani copy', () => {
  resetVillaniCopyCounters();
  const state = reduceVillaniUiState({ phase: 'x' }, { type: 'tool_started', tool: 'Read', path: 'a.ts' });
  assert.equal(state.phase, 'Villani reads file. File nervous.');
});

test('Write/Patch event sets writing Villani copy', () => {
  resetVillaniCopyCounters();
  assert.equal(reduceVillaniUiState({ phase: 'x' }, { type: 'tool_started', tool: 'Write' }).phase, 'Villani makes file obey...');
  assert.equal(reduceVillaniUiState({ phase: 'x' }, { type: 'tool_started', tool: 'Patch' }).phase, 'Villanipatch imposed...');
});

test('Bash command event sets running Villani copy', () => {
  resetVillaniCopyCounters();
  const state = reduceVillaniUiState({ phase: 'x' }, { type: 'tool_started', tool: 'Bash', input: { command: 'echo hi' } });
  assert.equal(state.phase, 'Villani gives command...');
});

test('pytest command event sets testing Villani copy', () => {
  resetVillaniCopyCounters();
  const state = reduceVillaniUiState({ phase: 'x' }, { type: 'tool_started', tool: 'Bash', input: { command: 'python -m pytest -q' } });
  assert.equal(state.phase, 'Villani begins inspection...');
});

test('run_completed uses complete Villani copy', () => {
  resetVillaniCopyCounters();
  assert.equal(reduceVillaniUiState({ phase: 'x' }, { type: 'run_completed' }).phase, 'Villanified. Accept result.');
});

test('run_failed uses failure Villani copy', () => {
  resetVillaniCopyCounters();
  assert.equal(reduceVillaniUiState({ phase: 'x' }, { type: 'run_failed', error: 'boom' }).phase, 'Villani sees failure. Unacceptable.');
});

test('villaniCopy rotates deterministically', () => {
  resetVillaniCopyCounters();
  assert.equal(villaniCopy('thinking'), 'Villani is make plan...');
  assert.equal(villaniCopy('thinking'), 'Villaniplan forming...');
  assert.equal(villaniCopy('thinking'), 'Villani thinks. Nobody interrupt.');
});

test('stream_text renders clean assistant text unchanged', async () => {
  resetVillaniUiState();
  const rec = ctxRecorder();
  await renderBridgeEvent({ type: 'stream_text', text: "\n\nI'll start by exploring the repository structure and finding the failing tests.\n\n" }, {}, rec.ctx);
  assert.deepEqual(rec.notifications, ["I'll start by exploring the repository structure and finding the failing tests."]);
});

test('cleanAssistantText trims without Villani prefix', () => {
  assert.equal(cleanAssistantText('\n\nhello\n\n'), 'hello');
});

test('approval message remains literal and readable', () => {
  const request = { tool: 'Bash', summary: 'Run command', input: { command: 'python -m pytest -v' } };
  assert.equal(approvalTitle(request), 'Villani wants to run a shell command');
  const msg = approvalMessage(request);
  assert.match(msg, /Command:\npython -m pytest -v/);
  assert.doesNotMatch(msg, /\[object Object\]/);
});
