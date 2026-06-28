from __future__ import annotations
import hashlib,json,io,os,queue,subprocess,sys,threading,traceback
from dataclasses import dataclass,field
from pathlib import Path
from typing import Any,Callable,TextIO
from villani_code.execution import ExecutionBudget, VILLANI_TASK_BUDGET
from villani_code.integrations.pi_bridge_protocol import *

HIDDEN={'.villani','.villani_code','__pycache__'}
def _visible(p:str)->bool:
    parts=p.replace('\\','/').split('/'); return not (any(x in HIDDEN for x in parts) or p.endswith('.pyc'))
def _cap(v:Any,n:int=2000)->str: return str(v)[:n]
def summarize_approval_request(tool_name:str, tool_input:dict[str,Any])->tuple[str,dict[str,Any]]:
    if tool_name=='Write':
        path=str(tool_input.get('path') or tool_input.get('file_path') or '')
        return f'Write file: {path}', {'path':path,'content_chars':len(str(tool_input.get('content','')))}
    if tool_name=='Patch':
        path=str(tool_input.get('path') or tool_input.get('file_path') or '')
        return f'Patch file: {path}', {'path':path,'operation':_cap(tool_input.get('operation') or 'patch',100)}
    if tool_name=='Bash': return 'Run command', {'command':_cap(tool_input.get('command',''))}
    out={k:_cap(tool_input[k]) for k in ('path','file_path','command') if k in tool_input}
    return tool_name,out

def git_changed_files(repo:Path)->list[str]:
    try:
        r=subprocess.run(
            ['git','status','--porcelain=v1','--untracked-files=all'],
            cwd=repo,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if r.returncode: return []
        files=[]
        for line in r.stdout.splitlines():
            name=line[3:] if len(line)>3 else ''
            if ' -> ' in name: name=name.split(' -> ',1)[1]
            if name and _visible(name): files.append(name)
        return sorted(set(files))
    except Exception: return []
def hash_file(path:Path)->str|None:
    try:
        if not path.is_file(): return None
        h=hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()
    except Exception: return None
def hash_files(repo:Path, files:list[str])->dict[str,str|None]: return {f:hash_file(repo/f) for f in files}
def attributed_changed_files(repo:Path,before_dirty:list[str],before_dirty_hashes:dict[str,str|None],touched_files:set[str]|list[str])->tuple[list[str],list[str]]:
    after=git_changed_files(repo); before=set(before_dirty); touched=set(touched_files); changed=[]; pre=[]
    for f in after:
        h=hash_file(repo/f)
        if f not in before or before_dirty_hashes.get(f)!=h or (f in touched and before_dirty_hashes.get(f)!=h): changed.append(f)
        else: pre.append(f)
    return sorted(set(filter(_visible,changed))), sorted(set(filter(_visible,pre)))

def map_runner_event(run_id:str,event:dict[str,Any])->list[dict[str,Any]]:
    t=event.get('type'); out=[]
    phase={'diagnosis_attempted','diagnosis_generated','planning_started','repair_attempt_started'}
    if t in phase: out.append({'type':'phase','id':run_id,'phase':t})
    elif t=='model_request_started': out += [{'type':'phase','id':run_id,'phase':t},{'type':'bridge_diagnostic','id':run_id,'message':'model request started'}]
    elif t in {'model_request_completed','model_request_failed'}: out.append({'type':'bridge_diagnostic','id':run_id,'message':t})
    elif t=='tool_started': out += [{'type':'tool_started','id':run_id,'tool':event.get('name')},{'type':'bridge_diagnostic','id':run_id,'message':'tool started'}]
    elif t=='tool_finished':
        out.append({'type':'tool_finished','id':run_id,'tool':event.get('name'),'is_error':event.get('is_error')})
        if event.get('name') in {'Write','Patch','Edit'}: out.append({'type':'workspace_changed','id':run_id})
    elif t=='validation_step_started': out.append({'type':'verification_started','id':run_id,'name':event.get('name')})
    elif t in {'validation_step_finished','validation_completed'}: out.append({'type':'verification_finished','id':run_id,'passed':event.get('passed')})
    elif t in {'command_wandering_detected','progress_governor_redirected','governor_redirect'}: out.append({'type':'governor_redirect','id':run_id,'reason':event.get('reason')})
    return out

def extract_summary(r:dict[str,Any])->str|None:
    for k in ('summary','response'):
        v=r.get(k)
        if isinstance(v,str): return v[:4000]
    return None

def run_existing_runner(runner:Any, command:RunCommand)->dict[str,Any]:
    budget=None
    if command.limits.max_turns is not None:
        budget=ExecutionBudget(max_turns=command.limits.max_turns,max_tool_calls=VILLANI_TASK_BUDGET.max_tool_calls,max_seconds=VILLANI_TASK_BUDGET.max_seconds,max_no_edit_turns=VILLANI_TASK_BUDGET.max_no_edit_turns,max_reconsecutive_recon_turns=VILLANI_TASK_BUDGET.max_reconsecutive_recon_turns)
    result=runner.run_villani_mode() if command.mode=='villani' else (runner.run(command.task,execution_budget=budget) if budget is not None else runner.run(command.task))
    return result if isinstance(result,dict) else {'response':result}

def build_default_runner(command:RunCommand,event_callback,approval_callback):
    from villani_code.runner_factory import build_runner
    provider=command.config.provider or os.environ.get('VILLANI_PROVIDER') or 'anthropic'; model=command.config.model or os.environ.get('VILLANI_MODEL'); base_url=command.config.base_url or os.environ.get('VILLANI_BASE_URL')
    if provider not in {'anthropic','openai'}: raise ValueError("provider must be 'anthropic' or 'openai'")
    if not model or not base_url: raise ValueError('run config requires model and base_url, or VILLANI_MODEL and VILLANI_BASE_URL')
    runner=build_runner(base_url=base_url,model=model,repo=Path(command.repo),provider=provider,api_key=command.config.api_key or os.environ.get('VILLANI_API_KEY'),villani_mode=command.mode=='villani',villani_objective=command.task if command.mode=='villani' else None,event_callback=event_callback,approval_callback=approval_callback,external_approval_mode=True)
    runner.print_stream=False; return runner
@dataclass(slots=True)
class PendingApproval:
    run_id:str; request_id:str; tool:str; ready:threading.Event=field(default_factory=threading.Event); approved:bool|None=None
@dataclass(slots=True)
class ActiveRun:
    command:RunCommand; abort_requested:threading.Event=field(default_factory=threading.Event); thread:threading.Thread|None=None; pending_approvals:dict[str,PendingApproval]=field(default_factory=dict); touched_files:set[str]=field(default_factory=set); approval_seq:int=0
class PiBridge:
    def __init__(self,*,stdin:TextIO|None=None,stdout:TextIO|None=None,stderr:TextIO|None=None,runner_factory:Callable[...,Any]|None=None)->None:
        self.stdin=stdin or sys.stdin; self.stdout=stdout or sys.stdout; self.stderr=stderr or sys.stderr; self.runner_factory=runner_factory or build_default_runner
        self._events: queue.Queue[dict[str, Any] | None]=queue.Queue(); self._active: dict[str, ActiveRun]={}; self._pending_approvals: dict[str, PendingApproval]={}; self._lock=threading.Lock()
        self.runs=self._active; self.lock=self._lock; self._stdio_running=False
    def emit(self,e):
        self.stdout.write(to_json_line(e)); self.stdout.flush()
    def _queue_event(self,e:dict[str,Any]|None):
        if self._stdio_running: self._events.put(e)
        elif e is not None: self.emit(e)
    def _diagnostic(self,run_id:str|None,message:str,**extra:Any)->None:
        ev={'type':'bridge_diagnostic','message':message}
        if run_id: ev['id']=run_id
        ev.update({k:v for k,v in extra.items() if k!='api_key'})
        self._queue_event(ev)
    def _drain_events(self)->None:
        while True:
            try: ev=self._events.get_nowait()
            except queue.Empty: return
            if ev is not None: self.emit(ev)
    def _stdin_reader(self,commands:queue.Queue[dict[str,Any]|None])->None:
        try:
            try: fd=self.stdin.fileno()
            except (AttributeError,io.UnsupportedOperation,OSError,ValueError,TypeError): fd=None  # type: ignore[name-defined]
            if fd is not None:
                buf=b''
                while True:
                    chunk=os.read(fd,4096)
                    if not chunk: break
                    buf += chunk
                    while b'\n' in buf:
                        raw,buf=buf.split(b'\n',1); line=raw.decode('utf-8',errors='replace')
                        if line.strip():
                            try: commands.put(parse_json_line(line))
                            except Exception as exc: self._queue_event({'type':'error','error':str(exc)})
                if buf.strip():
                    try: commands.put(parse_json_line(buf.decode('utf-8',errors='replace')))
                    except Exception as exc: self._queue_event({'type':'error','error':str(exc)})
            else:
                for raw_line in self.stdin:
                    if not str(raw_line).strip(): continue
                    try: commands.put(parse_json_line(str(raw_line)))
                    except Exception as exc: self._queue_event({'type':'error','error':str(exc)})
        finally:
            commands.put(None)
    def run_stdio(self)->None:
        self._stdio_running=True; self.emit(ready_event()); commands: queue.Queue[dict[str,Any]|None]=queue.Queue(); stdin_closed=False
        threading.Thread(target=self._stdin_reader,args=(commands,),daemon=True).start()
        while True:
            self._drain_events()
            if stdin_closed:
                with self._lock: active=bool(self._active)
                if not active: break
                try: cmd=commands.get(timeout=0.02)
                except queue.Empty: continue
            else:
                try: cmd=commands.get(timeout=0.02)
                except queue.Empty: continue
            if cmd is None:
                stdin_closed=True; continue
            self.handle(cmd)
    def run_forever(self):
        self.run_stdio()
    def handle(self,p):
        try:
            t=p.get('type')
            if t=='ping': self.emit({'type':'pong','id':p.get('id')}); return
            if t=='run': self.start_run(parse_run_command(p)); return
            if t=='abort': self.abort(str(p.get('id',''))); return
            if t=='approval_response': self.approval(parse_approval_response_command(p)); return
            self.emit({'type':'error','error':f'Unknown bridge command type: {t}'})
        except Exception as exc:
            self.emit({'type':'error','error':str(exc)})
    def start_run(self,cmd):
        repo=str(Path(cmd.repo))
        with self._lock:
            if cmd.id in self._active: self.emit({'type':'error','id':cmd.id,'error':'Duplicate active run id'}); return
            ar=ActiveRun(cmd); self._active[cmd.id]=ar
        self._queue_event({'type':'bridge_diagnostic','id':cmd.id,'message':'run command received'})
        self._queue_event({'type':'run_started','id':cmd.id,'run_id':cmd.id,'task':cmd.task,'repo':repo,'mode':cmd.mode})
        th=threading.Thread(target=self._worker,args=(ar,),daemon=True); ar.thread=th; th.start()
    def abort(self,run_id):
        with self._lock: ar=self._active.get(run_id)
        if not ar: self.emit({'type':'error','id':run_id,'error':'Unknown run id'}); return
        ar.abort_requested.set();
        for req,p in list(ar.pending_approvals.items()):
            ar.pending_approvals.pop(req,None)
            with self._lock: self._pending_approvals.pop(req,None)
            p.approved=False; p.ready.set()
        self.emit({'type':'abort_requested','id':run_id})
    def approval(self,cmd):
        with self._lock:
            p=self._pending_approvals.pop(cmd.request_id,None)
            ar=self._active.get(cmd.id)
            if p and ar: ar.pending_approvals.pop(cmd.request_id,None)
        if not ar: self.emit({'type':'error','id':cmd.id,'error':'Unknown run id'}); return
        if not p: self.emit({'type':'error','id':cmd.id,'error':'Unknown approval request id'}); return
        p.approved=cmd.approved; p.ready.set()
    def _worker(self,ar):
        cmd=ar.command; repo=Path(cmd.repo); before=[]; before_hash={}
        self._diagnostic(cmd.id,'run worker started')
        def ev(e):
            if e.get('type')=='approval_required': return
            for m in map_runner_event(cmd.id,e): self._queue_event(m)
        def appr(tool,inp):
            if ar.abort_requested.is_set(): return False
            title,summary=summarize_approval_request(tool,inp); ar.approval_seq += 1; req=f'{cmd.id}:{ar.approval_seq}'
            p=PendingApproval(cmd.id,req,tool)
            with self._lock: ar.pending_approvals[req]=p; self._pending_approvals[req]=p
            self._queue_event({'type':'approval_required','id':cmd.id,'request_id':req,'tool':tool,'title':title,'summary':summary})
            p.ready.wait(); self._queue_event({'type':'approval_resolved','id':cmd.id,'request_id':req,'approved':bool(p.approved)}); return bool(p.approved) and not ar.abort_requested.is_set()
        try:
            if ar.abort_requested.is_set(): self._queue_event({'type':'run_aborted','id':cmd.id}); return
            self._diagnostic(cmd.id,'capturing initial git status')
            before=git_changed_files(repo)
            before_hash=hash_files(repo,before)
            self._diagnostic(cmd.id,'captured initial git status')
            source='pi-proxy' if cmd.config.pi_model_proxy else 'direct-config'; self._diagnostic(cmd.id,f'model configuration source={source} provider={cmd.config.provider} model={cmd.config.model} base_url={cmd.config.base_url}')
            self._diagnostic(cmd.id,'creating runner')
            runner=self.runner_factory(cmd,ev,appr)
            self._diagnostic(cmd.id,'runner created; entering execution')
            result=run_existing_runner(runner,cmd)
            self._diagnostic(cmd.id,'runner.run returned')
            changed,pre=attributed_changed_files(repo,before,before_hash,ar.touched_files)
            if ar.abort_requested.is_set(): self._queue_event({'type':'run_aborted','id':cmd.id}); return
            ex=result.get('execution') if isinstance(result.get('execution'),dict) else {}; reason=ex.get('terminated_reason') or ex.get('reason')
            base={'id':cmd.id,'changed_files':changed,'preexisting_dirty_files':pre,'summary':extract_summary(result),'transcript_path':result.get('transcript_path'),'verification_passed':result.get('verification_passed'),'terminated_reason':reason}
            if ex.get('completed') is False: self._queue_event({'type':'run_failed','success':False,'error':str(reason or 'Runner stopped before completion.'),**base})
            else: self._queue_event({'type':'run_completed','success':True,**base})
        except Exception as exc:
            self._diagnostic(cmd.id,f'exception: {exc}')
            if ar.abort_requested.is_set(): self._queue_event({'type':'run_aborted','id':cmd.id}); return
            print(traceback.format_exc(),file=self.stderr); self._queue_event({'type':'run_failed','id':cmd.id,'success':False,'error':str(exc),'summary':str(exc)})
        finally:
            for req,p in list(ar.pending_approvals.items()):
                ar.pending_approvals.pop(req,None)
                with self._lock: self._pending_approvals.pop(req,None)
                p.approved=False; p.ready.set()
            with self._lock: self._active.pop(cmd.id,None)
def main_stdio(): PiBridge().run_stdio()
if __name__=='__main__': main_stdio()
