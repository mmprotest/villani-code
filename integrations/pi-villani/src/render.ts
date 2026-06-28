export async function notify(ctx: any, message: string, level: 'info'|'warn'|'error' = 'info'): Promise<void> {
  try { if (ctx?.ui?.notify) await ctx.ui.notify(message, level); else (level === 'error' ? console.error : console.log)(message); } catch { try { console.error(message); } catch {} }
}
export async function setStatus(ctx: any, message: string | undefined): Promise<void> { try { if (ctx?.ui?.setStatus) await ctx.ui.setStatus('villani', message); } catch { try { if (ctx?.ui?.setStatus) await ctx.ui.setStatus(message); } catch {} } }
export async function setWidget(ctx: any, widget: any): Promise<void> { try { if (ctx?.ui?.setWidget) await ctx.ui.setWidget('villani', widget); } catch {} }
export async function sendDurableVillaniMessage(pi: any, ctx: any, message: string, details?: any): Promise<void> {
  const payload = {
    customType: 'villani-result',
    content: [{ type: 'text', text: message }],
    display: true,
    details: sanitizeDetails(details),
  };

  try {
    if (typeof pi?.sendMessage === 'function') {
      await pi.sendMessage(payload);
      return;
    }
  } catch {
    // Fall back to notify below when Pi rejects the custom message.
  }

  await notify(ctx, message, 'info');
}
export async function confirm(ctx: any, title: string, message: string, options?: any): Promise<boolean> {
  try { if (ctx?.ui?.confirm) return !!(await ctx.ui.confirm(title, message, options)); } catch (e) { throw e; }
  return false;
}
export function visibleChangedFiles(files:string[]=[]){return files.filter(f=>!/(^|\/)(\.villani|\.villani_code|__pycache__)(\/|$)|\.pyc$/.test(f));}
function sanitizeDetails(value:any, seen=new WeakSet<object>()):any{
  if(value===undefined||value===null) return value;
  if(typeof value==='string'||typeof value==='number'||typeof value==='boolean') return value;
  if(Array.isArray(value)) return value.map(v=>sanitizeDetails(v,seen));
  if(typeof value==='object'){
    if(seen.has(value)) return '[Circular]';
    seen.add(value);
    const out:any={};
    for(const [key,entry] of Object.entries(value)){
      if(/(api[_-]?key|authorization|auth|bearer|token|secret|cookie|headers?)$/i.test(key)||/^(authorization|cookie)$/i.test(key)) continue;
      out[key]=sanitizeDetails(entry,seen);
    }
    seen.delete(value);
    return out;
  }
  return String(value);
}
function verificationStatus(event:any):string|undefined{const verification=event.verification_status??event.verificationStatus??event.verification; if(!verification)return undefined; if(typeof verification==='string') return `Verification: ${verification}`; if(typeof verification==='object'){const status=verification.status??verification.result??verification.outcome; return status?`Verification: ${status}`:undefined;} return `Verification: ${String(verification)}`;}
export function finalMessage(event:any){const files=visibleChangedFiles(event.changed_files||event.changedFiles||[]); const head=event.type==='run_completed'?'Villani completed':event.type==='run_aborted'?'Villani aborted':'Villani failed'; const transcript=event.transcript_path||event.transcriptPath; const verification=verificationStatus(event); return [head,event.summary||event.error||event.message,files.length?`Changed files:\n${files.map((f:string)=>`- ${f}`).join('\n')}`:'',transcript?`Transcript: ${transcript}`:'',verification].filter(Boolean).join('\n\n');}
let lastStreamAt=0;
export async function renderBridgeEvent(event:any, _pi:any, ctx:any): Promise<void> {
  const debug=process.env.VILLANI_PI_DEBUG==='1';
  const tool=String(event.tool??event.name??'unknown');
  const summary=String(event.summary??'').slice(0,500);
  const command=typeof event.command==='string'?event.command.slice(0,500):'';
  if(event.type==='approval_required') return;
  if(event.type==='bridge_diagnostic') { if(debug) await notify(ctx, `Villani diagnostic: ${event.message||event.error||'diagnostic'}`, 'info'); return; }
  if(event.type==='run_started') await notify(ctx, 'Villani run started.', 'info');
  else if(event.type==='model_request_started') await setStatus(ctx, 'Villani is thinking...');
  else if(event.type==='model_request_completed') await setStatus(ctx, 'Villani received model response');
  else if(event.type==='proxy_request_started') await setStatus(ctx, 'Villani is sending request to Pi model...');
  else if(event.type==='proxy_request_completed') await setStatus(ctx, 'Villani received model response');
  else if(event.type==='proxy_request_failed') await setStatus(ctx, 'Villani Pi model request failed');
  else if(event.type==='model_request_failed') await notify(ctx, `Villani model request failed: ${event.error||event.message||'unknown error'}`, 'error');
  else if(event.type==='tool_started') {
    if(tool==='Bash') await setStatus(ctx, 'Villani preparing command...');
    await notify(ctx, `Villani tool started: ${tool}${command?` — ${command}`:''}`, 'info');
  }
  else if(event.type==='command_started') { await setStatus(ctx, 'Villani running command'); await setWidget(ctx, ['Running command:', command]); }
  else if(event.type==='command_finished') {
    await setStatus(ctx, 'Command finished');
    const lines=[`Command finished: exit ${event.exit_code ?? 'unknown'}`];
    if(event.stderr_preview) lines.push(`stderr: ${String(event.stderr_preview).slice(0,500)}`);
    if(event.stdout_preview) lines.push(`stdout: ${String(event.stdout_preview).slice(0,500)}`);
    await setWidget(ctx, lines.join('\n'));
  }
  else if(event.type==='tool_result') await setStatus(ctx, 'Villani produced tool result for runner');
  else if(event.type==='tool_progress') { const msg=`Villani: ${String(event.message||'tool progress').slice(0,500)}`; await setStatus(ctx, msg); await notify(ctx, msg, 'info'); }
  else if(event.type==='tool_finished') {
    await setStatus(ctx, 'Villani finished tool handling');
    await notify(ctx, `Villani tool finished: ${tool}${summary?` — ${summary}`:''}`, event.ok===false||event.is_error?'warn':'info');
  }
  else if(event.type==='runner_heartbeat') await setStatus(ctx, `Villani is still running. Last runner event: ${String(event.last_event_type||'unknown')}.`);
  else if(event.type==='stream_text') { const text=String(event.text||'').trim().slice(0,240); const now=Date.now(); if(text&&now-lastStreamAt>1000){lastStreamAt=now; await setStatus(ctx, `Villani: ${text}`);} }
  else if(event.type==='error') await notify(ctx, `Villani error: ${event.error||event.message||'unknown error'}`, 'error');
}
