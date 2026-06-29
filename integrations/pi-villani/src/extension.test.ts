import test from "node:test";
import assert from "node:assert/strict";
import activate, { bridgePing } from "./index.js";
function api() {
  const commands: any = {};
  return {
    commands,
    registerCommand: (name: string, opts: any) => {
      commands[name] = opts;
      assert.equal(typeof opts.handler, "function");
    },
  };
}
test("registers only the public /villani command", () => {
  const a = api();
  activate(a);
  assert.deepEqual(Object.keys(a.commands), ["villani"]);
  for (const removed of [
    "villani-abort",
    "villani-confirm-test",
    "villani-ping",
    "villani-doctor",
    "villani-proxy-test",
    "villani-bridge-ping",
  ]) {
    assert.equal(a.commands[removed], undefined);
  }
});
import {
  chmodSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runVillani } from "./index.js";
function bridgeScript(body: string) {
  const d = mkdtempSync(join(tmpdir(), "pi-villani-run-"));
  const p = join(d, "bridge.mjs");
  writeFileSync(p, `#!/usr/bin/env node\n${body}`);
  chmodSync(p, 0o755);
  return p;
}
const readyPrelude =
  "process.stdout.write(JSON.stringify({type:'ready'})+'\\n');\nconst send=e=>process.stdout.write(JSON.stringify(e)+'\\n');\n";

test("/villani sends command notification and only reports run started after run_started event", async () => {
  const old = process.env.VILLANI_COMMAND;
  const p = bridgeScript(
    readyPrelude +
      "process.stdin.on('data',chunk=>{for(const line of chunk.toString().trim().split('\\n')){if(!line)continue; const msg=JSON.parse(line); if(msg.type==='ping'){send({type:'pong'}); continue;} send({type:'run_started',id:msg.id}); send({type:'phase',id:msg.id,name:'x'}); send({type:'run_completed',id:msg.id,summary:'done'});}}); setTimeout(()=>{},500);\n",
  );
  process.env.VILLANI_COMMAND = p;
  const notes: string[] = [];
  try {
    process.env.VILLANI_USE_PI_MODEL = "false";
    await runVillani(
      "task",
      { sendMessage: (m: string) => notes.push(m) },
      {
        ui: { notify: (m: string) => notes.push(m) },
        cwd: "/tmp",
        model: { id: "m" },
      },
    );
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
    delete process.env.VILLANI_USE_PI_MODEL;
  }
  const joined = notes.join("\n");
  assert.match(joined, /Villani starting/);
  assert.doesNotMatch(
    joined,
    /bridge event received|heartbeat pong|model request started/,
  );
});

test("/villani missing run_started timeout reports visible error instead of started", async () => {
  const old = process.env.VILLANI_COMMAND;
  const p = bridgeScript(
    readyPrelude +
      "process.stdin.on('data',chunk=>{for(const line of chunk.toString().trim().split('\\n')){if(!line)continue; const msg=JSON.parse(line); if(msg.type==='ping')send({type:'pong'});}}); console.error('no ack'); setTimeout(()=>{},15000);\n",
  );
  process.env.VILLANI_COMMAND = p;
  const notes: string[] = [];
  try {
    process.env.VILLANI_USE_PI_MODEL = "false";
    await assert.rejects(
      () =>
        runVillani(
          "task",
          {},
          { ui: { notify: (m: string) => notes.push(m) }, cwd: "/tmp" },
        ),
      /did not acknowledge run command within 10 seconds.*no ack/s,
    );
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
    delete process.env.VILLANI_USE_PI_MODEL;
  }
  assert.doesNotMatch(notes.join("\n"), /Villani run started\./);
});

async function waitForPidGone(pid: number, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      process.kill(pid, 0);
    } catch {
      return;
    }
    await new Promise((r) => setTimeout(r, 50));
  }
  assert.fail(`fake bridge process ${pid} remained alive`);
}

test("/villani run_started timeout cleans up fake bridge process", async () => {
  const oldCommand = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const d = mkdtempSync(join(tmpdir(), "pi-villani-cleanup-"));
  const pidFile = join(d, "pid");
  const p = join(d, "bridge.mjs");
  writeFileSync(
    p,
    `#!/usr/bin/env node
import { writeFileSync } from 'node:fs';
writeFileSync(${JSON.stringify(pidFile)},String(process.pid));
process.stdout.write(JSON.stringify({type:'ready'})+'\\n');
process.stdin.on('data',chunk=>{for(const line of chunk.toString().trim().split('\\n')){if(!line)continue; const msg=JSON.parse(line); if(msg.type==='ping')process.stdout.write(JSON.stringify({type:'pong'})+'\\n');}});
console.error('cleanup no ack');
setInterval(()=>{},1000);
`,
  );
  chmodSync(p, 0o755);
  process.env.VILLANI_COMMAND = p;
  process.env.VILLANI_USE_PI_MODEL = "false";
  const notes: string[] = [];
  try {
    await assert.rejects(
      () =>
        runVillani(
          "task",
          {},
          { ui: { notify: (m: string) => notes.push(m) }, cwd: "/tmp" },
        ),
      /did not acknowledge run command within 10 seconds.*cleanup no ack/s,
    );
    assert.ok(existsSync(pidFile));
    await waitForPidGone(Number(readFileSync(pidFile, "utf8")));
    assert.doesNotMatch(notes.join("\n"), /Villani run started\./);
  } finally {
    if (oldCommand === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = oldCommand;
    if (oldUsePi === undefined) delete process.env.VILLANI_USE_PI_MODEL;
    else process.env.VILLANI_USE_PI_MODEL = oldUsePi;
  }
});

test("bridge exit before ready with ModuleNotFoundError produces pip install diagnostic", async () => {
  const old = process.env.VILLANI_COMMAND;
  const p = bridgeScript(
    "console.error(\"ModuleNotFoundError: No module named 'villani_code'\"); process.exit(1);\n",
  );
  process.env.VILLANI_COMMAND = p;
  try {
    await assert.rejects(
      () => bridgePing({ ui: { notify: () => {} } }),
      /pip install -e/,
    );
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
  }
});

test("/villani-bridge-ping succeeds with fake bridge", async () => {
  const old = process.env.VILLANI_COMMAND;
  const p = bridgeScript(
    readyPrelude +
      "process.stdin.on('data',chunk=>{for(const line of chunk.toString().trim().split(/\\n/)){if(!line)continue; const msg=JSON.parse(line); if(msg.type==='ping')send({type:'pong'});}}); setTimeout(()=>{},500);\n",
  );
  process.env.VILLANI_COMMAND = p;
  const notes: string[] = [];
  try {
    await bridgePing({ ui: { notify: (m: string) => notes.push(m) } });
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
  }
  assert.match(notes.join("\n"), /Villani bridge ping succeeded/);
});

test("/villani-bridge-ping surfaces stderr on failure", async () => {
  const old = process.env.VILLANI_COMMAND;
  const p = bridgeScript("console.error('boom stderr'); process.exit(2);\n");
  process.env.VILLANI_COMMAND = p;
  try {
    await assert.rejects(
      () => bridgePing({ ui: { notify: () => {} } }),
      /boom stderr/,
    );
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
  }
});

test("/villani surfaces bridge error before run_started without waiting 10 seconds", async () => {
  const old = process.env.VILLANI_COMMAND;
  const p = bridgeScript(
    readyPrelude +
      "process.stdin.on('data',chunk=>{for(const line of chunk.toString().trim().split('\\n')){if(!line)continue; const msg=JSON.parse(line); if(msg.type==='ping'){send({type:'pong'}); continue;} if(msg.type==='run')send({type:'error',id:msg.id,error:'bad run command'});}}); setTimeout(()=>{},15000);\n",
  );
  process.env.VILLANI_COMMAND = p;
  const notes: string[] = [];
  const started = Date.now();
  try {
    process.env.VILLANI_USE_PI_MODEL = "false";
    await assert.rejects(
      () =>
        runVillani(
          "task",
          {},
          { ui: { notify: (m: string) => notes.push(m) }, cwd: "/tmp" },
        ),
      /bad run command/,
    );
    assert.ok(Date.now() - started < 5000);
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
    delete process.env.VILLANI_USE_PI_MODEL;
  }
  assert.doesNotMatch(notes.join("\n"), /Villani run started\./);
});

test("/villani launches bridge with ctx cwd", async () => {
  const old = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const d = mkdtempSync(join(tmpdir(), "pi-villani-cwd-"));
  const cwdFile = join(d, "cwd.txt");
  const p = bridgeScript(
    readyPrelude +
      `import { writeFileSync } from 'node:fs'; writeFileSync(${JSON.stringify(cwdFile)}, process.cwd()); process.stdin.on('data',chunk=>{for(const line of chunk.toString().trim().split('\\n')){if(!line)continue; const msg=JSON.parse(line); if(msg.type==='ping'){send({type:'pong'}); continue;} send({type:'run_started',id:msg.id}); send({type:'phase',id:msg.id,name:'x'}); send({type:'run_completed',id:msg.id,summary:'done'});}}); setTimeout(()=>{},500);\n`,
  );
  process.env.VILLANI_COMMAND = p;
  process.env.VILLANI_USE_PI_MODEL = "false";
  try {
    await runVillani(
      "task",
      { sendMessage: () => {} },
      { ui: { notify: () => {} }, cwd: d, model: { id: "m" } },
    );
    assert.equal(readFileSync(cwdFile, "utf8"), d);
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
    if (oldUsePi === undefined) delete process.env.VILLANI_USE_PI_MODEL;
    else process.env.VILLANI_USE_PI_MODEL = oldUsePi;
  }
});

test("VILLANI_PI_DEBUG sends bridge diagnostics to console not notify", async () => {
  const old = process.env.VILLANI_PI_DEBUG;
  const notes: string[] = [];
  const errs: string[] = [];
  const oldErr = console.error;
  try {
    process.env.VILLANI_PI_DEBUG = "1";
    console.error = (m: any) => errs.push(String(m));
    const { renderBridgeEvent } = await import("./render.js");
    await renderBridgeEvent(
      { type: "bridge_diagnostic", message: "capturing initial git status" },
      {},
      { ui: { notify: (m: string) => notes.push(m) } },
    );
    assert.equal(notes.length, 0);
    assert.match(errs.join("\n"), /capturing initial git status/);
  } finally {
    console.error = oldErr;
    if (old === undefined) delete process.env.VILLANI_PI_DEBUG;
    else process.env.VILLANI_PI_DEBUG = old;
  }
});

test("sendDurableVillaniMessage sends a structured iterable custom message", async () => {
  const { sendDurableVillaniMessage } = await import("./render.js");
  const sent: any[] = [];
  await sendDurableVillaniMessage(
    { sendMessage: (m: any) => sent.push(m) },
    { ui: { notify: () => assert.fail("notify should not be used") } },
    "hello",
    {
      type: "run_completed",
      authorization: "Bearer secret",
      headers: { authorization: "Bearer secret" },
      apiKey: "secret",
      token: "secret",
    },
  );
  assert.equal(sent.length, 1);
  assert.notEqual(typeof sent[0], "string");
  assert.equal(sent[0].customType, "villani-result");
  assert.equal(sent[0].display, true);
  assert.deepEqual(sent[0].content, [{ type: "text", text: "hello" }]);
  assert.ok(Symbol.iterator in Object(sent[0].content));
  assert.doesNotMatch(
    JSON.stringify(sent[0].details),
    /secret|authorization|apiKey|token/i,
  );
});

test("sendDurableVillaniMessage falls back to ctx.ui.notify if Pi sendMessage throws", async () => {
  const { sendDurableVillaniMessage } = await import("./render.js");
  const notes: string[] = [];
  await sendDurableVillaniMessage(
    {
      sendMessage: async () => {
        throw new Error("boom");
      },
    },
    {
      ui: { notify: (m: string, level: string) => notes.push(`${level}:${m}`) },
    },
    "fallback text",
  );
  assert.deepEqual(notes, ["info:fallback text"]);
});

test("renderBridgeEvent does not send durable final messages", async () => {
  const { renderBridgeEvent } = await import("./render.js");
  let sent = 0;
  const notes: string[] = [];
  for (const type of ["run_completed", "run_failed", "run_aborted"])
    await renderBridgeEvent(
      { type, summary: "done" },
      { sendMessage: () => sent++ },
      { ui: { notify: (m: string) => notes.push(m) } },
    );
  assert.equal(sent, 0);
  assert.deepEqual(notes, []);
});

test("/villani sends exactly one iterable final durable message with summary metadata", async () => {
  const old = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const p = bridgeScript(
    readyPrelude +
      "process.stdin.on('data',chunk=>{for(const line of chunk.toString().trim().split('\\n')){if(!line)continue; const msg=JSON.parse(line); if(msg.type==='ping'){send({type:'pong'}); continue;} send({type:'run_started',id:msg.id}); send({type:'model_request_started',id:msg.id}); send({type:'run_completed',id:msg.id,summary:'done',changed_files:['src/a.ts','.villani/secret'],transcript_path:'/tmp/transcript.jsonl',verification_status:'passed',authorization:'Bearer secret',headers:{authorization:'Bearer secret'}});}}); setTimeout(()=>{},500);\n",
  );
  const sent: any[] = [];
  try {
    process.env.VILLANI_COMMAND = p;
    process.env.VILLANI_USE_PI_MODEL = "false";
    await runVillani(
      "task",
      { sendMessage: (m: any) => sent.push(m) },
      { ui: { notify: () => {} }, cwd: "/tmp", model: { id: "m" } },
    );
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
    if (oldUsePi === undefined) delete process.env.VILLANI_USE_PI_MODEL;
    else process.env.VILLANI_USE_PI_MODEL = oldUsePi;
  }
  assert.equal(sent.length, 1);
  const payload = sent[0];
  assert.equal(payload.customType, "villani-result");
  assert.deepEqual(payload.content, [
    { type: "text", text: payload.content[0].text },
  ]);
  assert.ok(Array.isArray(payload.content));
  assert.match(payload.content[0].text, /Villani completed/);
  assert.match(payload.content[0].text, /done/);
  assert.match(payload.content[0].text, /src\/a\.ts/);
  assert.doesNotMatch(payload.content[0].text, /\.villani\/secret/);
  assert.match(payload.content[0].text, /Transcript: \/tmp\/transcript\.jsonl/);
  assert.match(payload.content[0].text, /Verification: passed/);
  assert.doesNotMatch(
    JSON.stringify(payload.details),
    /authorization|Bearer secret/i,
  );
});

test("approvalMessage never renders object and includes Bash command", async () => {
  const { approvalMessage, approvalTitle } = await import("./index.js");
  const msg = approvalMessage({
    tool: "Bash",
    summary: "Run command",
    input: { command: "echo hi" },
  });
  assert.doesNotMatch(msg, /\[object Object\]/);
  assert.match(msg, /Command: echo hi/);
  assert.equal(
    approvalTitle({ tool: "Bash" }),
    "Villani requests command authority",
  );
});

test("confirm passes signal option to ctx.ui.confirm", async () => {
  const { confirm } = await import("./render.js");
  const signal = new AbortController().signal;
  let args: any[] = [];
  const ok = await confirm(
    {
      ui: {
        confirm: async (...a: any[]) => {
          args = a;
          return true;
        },
      },
    },
    "t",
    "m",
    { signal },
  );
  assert.equal(ok, true);
  assert.equal(args[2].signal, signal);
});

test("/villani approval pending clears widget, sets status, confirms, and accepted clears widget", async () => {
  const old = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const p = bridgeScript(
    readyPrelude +
      "process.stdin.on('data',chunk=>{for(const line of chunk.toString().trim().split('\\n')){if(!line)continue; const msg=JSON.parse(line); if(msg.type==='ping'){send({type:'pong'}); continue;} if(msg.type==='approval_response'){send({type:'tool_progress',id:msg.id,tool:'Bash',message:'Running command: echo hi'}); send({type:'run_completed',id:msg.id,summary:'done'}); continue;} send({type:'run_started',id:msg.id}); send({type:'approval_required',id:msg.id,request_id:msg.id+':1',tool:'Bash',summary:'Run command',input:{command:'echo hi'}});}}); setTimeout(()=>{},500);\n",
  );
  const statuses: any[] = [];
  const widgets: any[] = [];
  const confirms: any[] = [];
  try {
    process.env.VILLANI_COMMAND = p;
    process.env.VILLANI_USE_PI_MODEL = "false";
    await runVillani(
      "task",
      { sendMessage: () => {} },
      {
        ui: {
          notify: () => {},
          setStatus: (...a: any[]) => statuses.push(a),
          setWidget: (...a: any[]) => widgets.push(a),
          confirm: async (...a: any[]) => {
            confirms.push(a);
            return true;
          },
        },
        cwd: "/tmp",
        model: { id: "m" },
      },
    );
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
    if (oldUsePi === undefined) delete process.env.VILLANI_USE_PI_MODEL;
    else process.env.VILLANI_USE_PI_MODEL = oldUsePi;
  }
  assert.ok(
    statuses.some((a) => a[0] === "villani" && /approval|authorization|authority|clearance/i.test(String(a[1]))),
  );
  assert.ok(widgets.length > 0);
  assert.equal(widgets[0][0], "villani");
  assert.equal(widgets[0][1], undefined);
  assert.equal(confirms[0][0], "Villani requests command authority");
  assert.match(confirms[0][1], /Command: echo hi/);
  assert.match(confirms[0][1], /Approve this Villani action\?/);
  assert.doesNotMatch(JSON.stringify(widgets), /Villani requests command authority|Command: echo hi|Pending approval|Allow this operation|\[object Object\]/);
  assert.ok(widgets.some((a) => a[0] === "villani" && a[1] === undefined));
  assert.equal(confirms[0][2].signal instanceof AbortSignal, true);
});

test("tool result and finished events update status without notify spam", async () => {
  const { renderBridgeEvent } = await import("./render.js");
  const notes: string[] = [];
  const statuses: string[] = [];
  await renderBridgeEvent(
    {
      type: "tool_finished",
      tool: "Bash",
      summary: "Command finished: exit 0",
      ok: true,
    },
    {},
    {
      ui: {
        notify: (m: string) => notes.push(m),
        setStatus: (_: string, m: string) => statuses.push(m),
      },
    },
  );
  await renderBridgeEvent(
    { type: "tool_progress", message: "Running command: echo hi" },
    {},
    {
      ui: {
        notify: (m: string) => notes.push(m),
        setStatus: (_: string, m: string) => statuses.push(m),
      },
    },
  );
  assert.deepEqual(notes, []);
  assert.deepEqual(statuses, []);
});

test("/villani keeps waiting after nonzero tool_finished and renders next model event", async () => {
  const old = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const p = bridgeScript(
    readyPrelude +
      "process.stdin.on('data',chunk=>{for(const line of chunk.toString().trim().split('\\n')){if(!line)continue; const msg=JSON.parse(line); if(msg.type==='ping'){send({type:'pong'}); continue;} send({type:'run_started',id:msg.id}); send({type:'tool_finished',id:msg.id,tool:'Bash',ok:false,is_error:true,summary:'Bash finished: exit 255\\nstderr: head not recognized'}); setTimeout(()=>send({type:'model_request_started',id:msg.id}),50); setTimeout(()=>send({type:'run_completed',id:msg.id,summary:'done'}),100);}}); setTimeout(()=>{},500);\n",
  );
  const statuses: string[] = [];
  const notes: string[] = [];
  const sent: any[] = [];
  try {
    process.env.VILLANI_COMMAND = p;
    process.env.VILLANI_USE_PI_MODEL = "false";
    await runVillani(
      "task",
      { sendMessage: (m: any) => sent.push(m) },
      {
        ui: {
          notify: (m: string) => notes.push(m),
          setStatus: (_: string, m: string) => statuses.push(m),
        },
        cwd: "/tmp",
        model: { id: "m" },
      },
    );
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
    if (oldUsePi === undefined) delete process.env.VILLANI_USE_PI_MODEL;
    else process.env.VILLANI_USE_PI_MODEL = oldUsePi;
  }
  assert.doesNotMatch(notes.join("\n"), /Villani tool finished/);
  assert.ok(statuses.some((s) => /^Villani|^Villani/.test(s)));
  assert.equal(sent.length, 1);
});

test("repeated model_request_started updates status without notify spam", async () => {
  const { renderBridgeEvent } = await import("./render.js");
  const statuses: string[] = [];
  const notes: string[] = [];
  const ctx = {
    ui: {
      setStatus: (_: string, m: string) => statuses.push(m),
      notify: (m: string) => notes.push(m),
    },
  };
  await renderBridgeEvent({ type: "model_request_started" }, {}, ctx);
  await renderBridgeEvent({ type: "model_request_started" }, {}, ctx);
  assert.equal(statuses.length, 1);
  assert.ok(statuses.every((s) => /^Villani/.test(s)));
  assert.deepEqual(notes, []);
});

test("bridge heartbeat pong never calls notify, including debug", async () => {
  const { renderBridgeEvent } = await import("./render.js");
  const old = process.env.VILLANI_PI_DEBUG;
  const notes: string[] = [];
  try {
    delete process.env.VILLANI_PI_DEBUG;
    await renderBridgeEvent(
      { type: "pong" },
      {},
      { ui: { notify: (m: string) => notes.push(m) } },
    );
    process.env.VILLANI_PI_DEBUG = "1";
    await renderBridgeEvent(
      { type: "bridge_diagnostic", message: "bridge heartbeat pong" },
      {},
      { ui: { notify: (m: string) => notes.push(m) } },
    );
    assert.deepEqual(notes, []);
  } finally {
    if (old === undefined) delete process.env.VILLANI_PI_DEBUG;
    else process.env.VILLANI_PI_DEBUG = old;
  }
});

test("bridge_diagnostic does not notify unless debug enabled and debug uses console", async () => {
  const { renderBridgeEvent } = await import("./render.js");
  const old = process.env.VILLANI_PI_DEBUG;
  const oldErr = console.error;
  const notes: string[] = [];
  const errs: string[] = [];
  try {
    delete process.env.VILLANI_PI_DEBUG;
    await renderBridgeEvent(
      { type: "bridge_diagnostic", message: "model request started" },
      {},
      { ui: { notify: (m: string) => notes.push(m) } },
    );
    process.env.VILLANI_PI_DEBUG = "1";
    console.error = (m: any) => errs.push(String(m));
    await renderBridgeEvent(
      { type: "bridge_diagnostic", message: "model request started" },
      {},
      { ui: { notify: (m: string) => notes.push(m) } },
    );
    assert.deepEqual(notes, []);
    assert.match(errs.join("\n"), /model request started/);
  } finally {
    console.error = oldErr;
    if (old === undefined) delete process.env.VILLANI_PI_DEBUG;
    else process.env.VILLANI_PI_DEBUG = old;
  }
});

test("command lifecycle sets then clears widget through final result", async () => {
  const old = process.env.VILLANI_COMMAND;
  const oldUsePi = process.env.VILLANI_USE_PI_MODEL;
  const p = bridgeScript(
    readyPrelude +
      "process.stdin.on('data',chunk=>{for(const line of chunk.toString().trim().split('\\n')){if(!line)continue; const msg=JSON.parse(line); if(msg.type==='ping'){send({type:'pong'}); continue;} send({type:'run_started',id:msg.id}); send({type:'command_started',id:msg.id,command:'echo hi'}); send({type:'command_finished',id:msg.id,command:'echo hi',exit_code:0,stdout_preview:'hi'}); send({type:'run_completed',id:msg.id,summary:'done'});}}); setTimeout(()=>{},500);\n",
  );
  const widgets: any[] = [];
  const sent: any[] = [];
  try {
    process.env.VILLANI_COMMAND = p;
    process.env.VILLANI_USE_PI_MODEL = "false";
    await runVillani(
      "task",
      { sendMessage: (m: any) => sent.push(m) },
      {
        ui: {
          notify: () => {},
          setWidget: (...a: any[]) => widgets.push(a),
          setStatus: () => {},
        },
        cwd: "/tmp",
        model: { id: "m" },
      },
    );
  } finally {
    if (old === undefined) delete process.env.VILLANI_COMMAND;
    else process.env.VILLANI_COMMAND = old;
    if (oldUsePi === undefined) delete process.env.VILLANI_USE_PI_MODEL;
    else process.env.VILLANI_USE_PI_MODEL = oldUsePi;
  }
  assert.ok(widgets.some((a) => a[0] === "villani" && a[1] === undefined));
  assert.equal(sent.length, 1);
});

test("strict render allowlist suppresses bridge plumbing", async () => {
  const { renderBridgeEvent, shouldRenderUserFacingEvent } = await import("./render.js");
  const calls: any[] = [];
  const ctx = {
    ui: {
      notify: (...a: any[]) => calls.push(["notify", ...a]),
      setStatus: (...a: any[]) => calls.push(["setStatus", ...a]),
      setWidget: (...a: any[]) => calls.push(["setWidget", ...a]),
    },
  };
  for (const event of [
    { type: "bridge_diagnostic", message: "event received: tool_result" },
    { type: "bridge_diagnostic", message: "bridge heartbeat pong" },
    { type: "runner_heartbeat" },
    { type: "pong" },
    { type: "tool_result", summary: "tool_result mapped" },
  ]) {
    assert.equal(shouldRenderUserFacingEvent(event), false);
    await renderBridgeEvent(event, {}, ctx);
  }
  assert.deepEqual(calls, []);
});

test("stream_text renders clean assistant blocks and suppresses duplicates/whitespace", async () => {
  const { renderBridgeEvent, resetVillaniUiState } = await import("./render.js");
  resetVillaniUiState();
  const notes: string[] = [];
  const ctx = { ui: { notify: (m: string) => notes.push(m) } };
  await renderBridgeEvent({ type: "stream_text", text: "\n\nI'll start...\n\n" }, {}, ctx);
  await renderBridgeEvent({ type: "stream_text", text: "I'll start..." }, {}, ctx);
  await renderBridgeEvent({ type: "stream_text", text: "\n \t\n" }, {}, ctx);
  assert.deepEqual(notes, ["I'll start..."]);
});

test("tool and command events render readable English", async () => {
  const { renderBridgeEvent, resetVillaniUiState } = await import("./render.js");
  resetVillaniUiState();
  const notes: string[] = [];
  const widgets: any[] = [];
  const statuses: string[] = [];
  const ctx = {
    ui: {
      notify: (m: string) => notes.push(m),
      setWidget: (_: string, w: any) => widgets.push(w),
      setStatus: (_: string, s: string) => statuses.push(s),
    },
  };
  await renderBridgeEvent({ type: "tool_started", tool: "Bash" }, {}, ctx);
  await renderBridgeEvent({ type: "command_started", command: "python -m pytest -v" }, {}, ctx);
  await renderBridgeEvent({ type: "command_finished", command: "python -m pytest -v", exit_code: 1, stderr_preview: "boom" }, {}, ctx);
  assert.deepEqual(notes, ["Command finished: exit 1\n\nstderr:\nboom"]);
  assert.doesNotMatch(notes.join("\n"), /tool_started|command_started|command_finished|Preparing command|Running command:/);
  assert.ok(statuses.some((s) => /^Villani/.test(s)));
  assert.ok(widgets.some((w) => String(w).includes("python -m pytest -v")));
});

test("model request clears stale command widget and final clears widget", async () => {
  const { renderBridgeEvent, resetVillaniUiState } = await import("./render.js");
  resetVillaniUiState();
  const widgets: any[] = [];
  const statuses: string[] = [];
  const ctx = {
    ui: {
      notify: () => {},
      setWidget: (_: string, w: any) => widgets.push(w),
      setStatus: (_: string, s: string) => statuses.push(s),
    },
  };
  await renderBridgeEvent({ type: "command_started", command: "echo hi" }, {}, ctx);
  await renderBridgeEvent({ type: "command_finished", command: "echo hi", exit_code: 0, stdout_preview: "hi" }, {}, ctx);
  await renderBridgeEvent({ type: "model_request_started" }, {}, ctx);
  await renderBridgeEvent({ type: "run_completed", summary: "final assistant summary" }, {}, ctx);
  assert.ok(widgets.some((w) => String(w).includes("Command finished: exit 0")));
  assert.ok(widgets.some((w) => w === undefined));
  assert.ok(statuses.some((s) => /^Villani/.test(s)));
});

test("final message includes final assistant summary", async () => {
  const { finalMessage } = await import("./render.js");
  const msg = finalMessage({
    type: "run_completed",
    summary: "final assistant summary",
    changed_files: ["src/a.ts"],
    transcript_path: "/tmp/t.jsonl",
  });
  assert.match(msg, /Villani completed/);
  assert.match(msg, /final assistant summary/);
  assert.match(msg, /Changed files:\n- src\/a\.ts/);
  assert.match(msg, /Transcript: \/tmp\/t\.jsonl/);
});
