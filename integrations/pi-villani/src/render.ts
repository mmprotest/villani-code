export async function notify(ctx: any, message: string, level: 'info'|'warn'|'error' = 'info'): Promise<void> {
  try { if (ctx?.ui?.notify) await ctx.ui.notify(message, level); else (level === 'error' ? console.error : console.log)(message); } catch { try { console.error(message); } catch {} }
}
export async function setStatus(ctx: any, message: string | undefined): Promise<void> { try { if (ctx?.ui?.setStatus) await ctx.ui.setStatus(message); } catch {} }
export async function sendMessage(pi: any, message: string): Promise<void> {
  try {
    if (pi?.sendMessage) await pi.sendMessage(message);
    else if (pi?.ui?.sendMessage) await pi.ui.sendMessage(message);
    else if (pi?.ctx?.ui?.notify) await pi.ctx.ui.notify(message, 'info');
    else console.log(message);
  } catch { try { console.log(message); } catch {} }
}
export async function confirm(ctx: any, title: string, message: string): Promise<boolean> {
  try { if (ctx?.ui?.confirm) return !!(await ctx.ui.confirm(title, message)); } catch (e) { throw e; }
  return false;
}
export function visibleChangedFiles(files:string[]=[]){return files.filter(f=>!/(^|\/)(\.villani|\.villani_code|__pycache__)(\/|$)|\.pyc$/.test(f));}
export function finalMessage(event:any){const files=visibleChangedFiles(event.changed_files||[]); const head=event.type==='run_completed'?'Villani completed':event.type==='run_aborted'?'Villani aborted':'Villani failed'; return [head,event.summary||event.message,files.length?`Changed files:\n${files.map((f:string)=>`- ${f}`).join('\n')}`:''].filter(Boolean).join('\n\n');}
export async function renderBridgeEvent(event:any, pi:any, ctx:any): Promise<void> { const debug=process.env.VILLANI_PI_DEBUG==='1'; if(event.type==='bridge_diagnostic'&&!debug)return; const progress = new Set(['ready','run_started','phase','tool_started','tool_finished','workspace_changed','verification_started','verification_finished','governor_redirect','approval_resolved']); if(progress.has(event.type)) await notify(ctx, `Villani: ${event.type}`, 'info'); if(event.type==='error') await notify(ctx, `Villani error: ${event.message||'unknown error'}`, 'error'); if(['run_completed','run_failed','run_aborted'].includes(event.type)) await sendMessage(pi, finalMessage(event)); }
