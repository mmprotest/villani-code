export async function notify(ctx: any, message: string, level: 'info'|'warn'|'error' = 'info'): Promise<void> {
  try { if (ctx?.ui?.notify) await ctx.ui.notify(message, level); else (level === 'error' ? console.error : console.log)(message); } catch { try { console.error(message); } catch {} }
}
export async function setStatus(ctx: any, message: string | undefined): Promise<void> { try { if (ctx?.ui?.setStatus) await ctx.ui.setStatus(message); } catch {} }
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
export async function confirm(ctx: any, title: string, message: string): Promise<boolean> {
  try { if (ctx?.ui?.confirm) return !!(await ctx.ui.confirm(title, message)); } catch (e) { throw e; }
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
export async function renderBridgeEvent(event:any, _pi:any, ctx:any): Promise<void> { const debug=process.env.VILLANI_PI_DEBUG==='1'; if(event.type==='bridge_diagnostic'&&!debug)return; if(event.type==='bridge_diagnostic'&&debug) await notify(ctx, `Villani diagnostic: ${event.message||event.error||'diagnostic'}`, 'info'); else if(event.type==='run_started') await notify(ctx, 'Villani run started.', 'info'); else if(event.type==='model_request_started') await notify(ctx, 'Villani diagnostic: model request started', 'info'); else if(event.type==='model_request_completed') await notify(ctx, 'Villani diagnostic: model response received', 'info'); else if(event.type==='tool_started') await notify(ctx, `Villani tool started: ${String(event.tool??event.name??'unknown')}`, 'info'); else if(event.type==='tool_finished') await notify(ctx, `Villani tool finished: ${String(event.tool??event.name??'unknown')}`, 'info'); else if(event.type==='stream_text'&&debug) await notify(ctx, `Villani: ${String(event.text||'').slice(0,240)}`, 'info'); else if(new Set(['ready','phase','workspace_changed','verification_started','verification_finished','governor_redirect','approval_resolved']).has(event.type)) await notify(ctx, `Villani: ${event.type}`, 'info'); if(event.type==='error') await notify(ctx, `Villani error: ${event.error||event.message||'unknown error'}`, 'error'); }
