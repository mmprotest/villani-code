from __future__ import annotations

import io, json, os, subprocess, tempfile, time, threading
from pathlib import Path

from villani_code.execution import ExecutionBudget
from villani_code.integrations.pi_bridge import PiBridge, attributed_changed_files, git_changed_files, hash_files, run_existing_runner

class DummyRunner:
    def __init__(self, approval_callback=None, event_callback=None): self.approval_callback=approval_callback; self.event_callback=event_callback; self.calls=[]
    def run(self, task, execution_budget=None):
        self.calls.append(('run',task,execution_budget))
        if task=='raise': raise RuntimeError('boom')
        if task=='incomplete': return {'response':'bad','execution':{'completed':False,'reason':'budget'}}
        if task=='stream': self.event_callback({'type':'stream_text','text':'SECRET'}); return {'response':'ok','execution':{'completed':True}}
        if task=='write':
            if self.approval_callback('Write', {'path':'a.txt','content':'x'}): Path('a.txt').write_text('x')
            return {'response':'ok','execution':{'completed':True}}
        if task=='bash': self.approval_callback('Bash', {'command':'echo hello && cat secret'}); return {'response':'ok','execution':{'completed':True}}
        if task=='approve-twice':
            assert self.approval_callback('Bash', {'command':'one'}) is True
            assert self.approval_callback('Bash', {'command':'two'}) is True
        return {'response':'ok','execution':{'completed':True},'verification_passed':True}
    def run_villani_mode(self): self.calls.append(('villani',)); return {'response':'villani','execution':{'completed':True}}

def make_bridge(runner=None):
    out=io.StringIO(); err=io.StringIO(); made=[]
    def factory(cmd,event_callback,approval_callback):
        r=runner or DummyRunner(approval_callback,event_callback); r.approval_callback=approval_callback; r.event_callback=event_callback; made.append(r); return r
    return PiBridge(stdin=io.StringIO(),stdout=out,stderr=err,runner_factory=factory),out,err,made

def events(out): return [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
def wait_for(out, pred, timeout=3):
    end=time.time()+timeout
    while time.time()<end:
        es=events(out)
        if any(pred(e) for e in es): return es
        time.sleep(0.01)
    raise AssertionError(events(out))
def run_cmd(repo, task='ok', id='r1', **extra):
    d={'type':'run','id':id,'task':task,'repo':str(repo),'config':{'provider':'openai','model':'m','base_url':'u'}}; d.update(extra); return d

def test_malformed_json_emits_error_and_bridge_continues():
    b,out,_,_=make_bridge(); b.run_forever.__self__.stdin=io.StringIO('{bad\n{"type":"ping","id":"p"}\n'); b.run_forever(); assert [e['type'] for e in events(out)]==['ready','error','pong']

def test_unknown_command_emits_error():
    b,out,_,_=make_bridge(); b.handle({'type':'wat'}); assert events(out)[0]['type']=='error'

def test_duplicate_run_id_is_rejected(tmp_path):
    b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,'approve-twice')); wait_for(out,lambda e:e['type']=='approval_required'); b.handle(run_cmd(tmp_path,'ok')); assert any(e['type']=='error' and 'Duplicate' in e['error'] for e in events(out)); b.abort('r1')

def test_runner_exception_and_incomplete_emit_run_failed(tmp_path):
    for task,msg in [('raise','boom'),('incomplete','budget')]:
        b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,task)); wait_for(out,lambda e:e['type']=='run_failed'); assert any(e['type']=='run_failed' and msg in e.get('error','') for e in events(out))

def test_completed_runner_emits_run_completed(tmp_path):
    b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path)); wait_for(out,lambda e:e['type']=='run_completed'); assert events(out)[-1]['success'] is True

def test_modes_and_max_turns_are_passed(tmp_path):
    r=DummyRunner(); b,out,_,made=make_bridge(r); b.handle(run_cmd(tmp_path,mode='runner',limits={'max_turns':7})); wait_for(out,lambda e:e['type']=='run_completed'); assert made[0].calls[0][0]=='run'; assert isinstance(made[0].calls[0][2],ExecutionBudget); assert made[0].calls[0][2].max_turns==7
    r2=DummyRunner(); b,out,_,made=make_bridge(r2); b.handle(run_cmd(tmp_path,id='r2',mode='villani')); wait_for(out,lambda e:e['type']=='run_completed'); assert made[0].calls[0][0]=='villani'

def test_stream_text_is_capped_and_executionplan_approval_events_are_not_exposed(tmp_path):
    b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,'stream')); wait_for(out,lambda e:e['type']=='run_completed'); assert any(e['type']=='stream_text' and e['text']=='SECRET' for e in events(out)); assert 'approval_required' not in [e['type'] for e in events(out)]

def test_write_approval_blocks_and_rejected_write_does_not_mutate_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,'write')); wait_for(out,lambda e:e['type']=='approval_required'); assert not (tmp_path/'a.txt').exists(); b.approval(type('C',(),{'id':'r1','request_id':'r1:1','approved':False})()); wait_for(out,lambda e:e['type']=='run_completed'); assert not (tmp_path/'a.txt').exists()

def test_bash_approval_summary_is_concise(tmp_path):
    b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,'bash')); wait_for(out,lambda e:e['type']=='approval_required'); e=[x for x in events(out) if x['type']=='approval_required'][0]; assert e['summary']=='Run command'; assert e['input']=={'command':'echo hello && cat secret'}; b.abort('r1')

def test_duplicate_unknown_and_malformed_approval_responses(tmp_path):
    b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,'approve-twice')); wait_for(out,lambda e:e['type']=='approval_required'); b.handle({'type':'approval_response','id':'r1','request_id':'r1:1','approved':True}); wait_for(out,lambda e:e.get('request_id')=='r1:2'); b.handle({'type':'approval_response','id':'r1','request_id':'r1:1','approved':False}); b.handle({'type':'approval_response','id':'r1','request_id':'nope','approved':True}); b.handle({'type':'approval_response','id':'r1','request_id':'r1:2','approved':'yes'}); assert sum(1 for e in events(out) if e['type']=='error')>=3; b.approval(type('C',(),{'id':'r1','request_id':'r1:2','approved':True})()); wait_for(out,lambda e:e['type']=='run_completed')

def test_abort_during_pending_approval_emits_run_aborted(tmp_path):
    b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,'approve-twice')); wait_for(out,lambda e:e['type']=='approval_required'); b.abort('r1'); wait_for(out,lambda e:e['type']=='run_aborted'); assert any(e['type']=='approval_resolved' and e['approved'] is False for e in events(out))

def init_repo(p):
    subprocess.run(['git','init'],cwd=p,check=True,capture_output=True); subprocess.run(['git','config','user.email','a@b.c'],cwd=p,check=True); subprocess.run(['git','config','user.name','A'],cwd=p,check=True)

def test_changed_file_attribution_cases(tmp_path):
    init_repo(tmp_path); (tmp_path/'tracked.txt').write_text('base'); subprocess.run(['git','add','.'],cwd=tmp_path,check=True); subprocess.run(['git','commit','-m','init'],cwd=tmp_path,check=True,capture_output=True)
    (tmp_path/'new.txt').write_text('n'); before=git_changed_files(tmp_path); hashes=hash_files(tmp_path,before); ch,pre=attributed_changed_files(tmp_path,before,hashes,set()); assert 'new.txt' in pre and not ch
    (tmp_path/'new2.txt').write_text('n'); ch,pre=attributed_changed_files(tmp_path,before,hashes,set()); assert 'new2.txt' in ch and 'new.txt' in pre
    (tmp_path/'new.txt').write_text('changed'); ch,pre=attributed_changed_files(tmp_path,before,hashes,set()); assert 'new.txt' in ch
    (tmp_path/'revert.txt').write_text('x'); (tmp_path/'revert.txt').unlink(); ch,_=attributed_changed_files(tmp_path,before,hashes,set()); assert 'revert.txt' not in ch
    for path in ['.villani/a','.villani_code/b','__pycache__/c.pyc','d.pyc']:
        q=tmp_path/path; q.parent.mkdir(parents=True,exist_ok=True); q.write_text('x')
    ch,pre=attributed_changed_files(tmp_path,before,hashes,set()); assert not any(x.startswith(('.villani','.villani_code','__pycache__')) or x.endswith('.pyc') for x in ch+pre)

class BlockingRunner(DummyRunner):
    def __init__(self, started=None, release=None, approval_callback=None, event_callback=None):
        super().__init__(approval_callback,event_callback); self.started=started or threading.Event(); self.release=release or threading.Event()
    def run(self, task, execution_budget=None):
        self.started.set(); self.release.wait(2); return {'response':'ok','execution':{'completed':True}}

def run_stdio_in_thread(bridge):
    th=threading.Thread(target=bridge.run_stdio,daemon=True); th.start(); return th

def test_run_stdio_emits_ready():
    b,out,_,_=make_bridge(); b.stdin=io.StringIO(''); b.run_stdio(); assert events(out)[0]['type']=='ready'

def test_run_stdio_accepts_ping_and_returns_pong_without_stdin_eof():
    r,w=os.pipe(); inp=os.fdopen(r,'r'); out=io.StringIO(); b=PiBridge(stdin=inp,stdout=out,runner_factory=lambda *a: DummyRunner())
    th=run_stdio_in_thread(b); wait_for(out,lambda e:e['type']=='ready')
    os.write(w,b'{"type":"ping","id":"p1"}\n'); wait_for(out,lambda e:e['type']=='pong' and e.get('id')=='p1')
    assert th.is_alive(); os.close(w); th.join(2)

def test_run_stdio_accepts_run_command_and_emits_run_started(tmp_path):
    b,out,_,_=make_bridge(); b.stdin=io.StringIO(json.dumps(run_cmd(tmp_path))+"\n"); b.run_stdio(); assert any(e['type']=='run_started' for e in events(out))

def test_run_stdio_keeps_running_after_stdin_closes_while_active_run_is_running(tmp_path):
    started=threading.Event(); release=threading.Event(); runner=BlockingRunner(started,release)
    b,out,_,_=make_bridge(runner); b.stdin=io.StringIO(json.dumps(run_cmd(tmp_path))+"\n")
    th=run_stdio_in_thread(b); wait_for(out,lambda e:e['type']=='run_started'); assert started.wait(1); time.sleep(0.05); assert th.is_alive(); release.set(); th.join(2); assert not th.is_alive()

def test_run_started_is_emitted_before_runner_factory_is_called(tmp_path):
    out=io.StringIO(); order=[]
    def factory(cmd,event_callback,approval_callback):
        order.append([e['type'] for e in events(out)]); return DummyRunner(approval_callback,event_callback)
    b=PiBridge(stdin=io.StringIO(json.dumps(run_cmd(tmp_path))+"\n"),stdout=out,runner_factory=factory); b.run_stdio()
    assert 'run_started' in order[0]

def test_bridge_error_before_run_started_is_surfaced_by_stdio_loop():
    b,out,_,_=make_bridge(); b.stdin=io.StringIO('{"type":"run","id":"r"}\n'); b.run_stdio(); es=events(out); assert es[0]['type']=='ready'; assert any(e['type']=='error' and 'task is required' in e['error'] for e in es)

def test_worker_events_are_drained_from_queue(tmp_path):
    b,out,_,_=make_bridge(); b.stdin=io.StringIO(json.dumps(run_cmd(tmp_path,'stream'))+"\n"); b.run_stdio(); assert any(e['type']=='run_completed' for e in events(out)); assert b._events.empty()

def test_approval_response_works_through_queued_stdin_command(tmp_path):
    r,w=os.pipe(); inp=os.fdopen(r,'r'); out=io.StringIO(); b=PiBridge(stdin=inp,stdout=out,runner_factory=lambda *a: DummyRunner(a[2],a[1]))
    th=run_stdio_in_thread(b); wait_for(out,lambda e:e['type']=='ready')
    os.write(w,(json.dumps(run_cmd(tmp_path,'write'))+'\n').encode()); wait_for(out,lambda e:e['type']=='approval_required')
    os.write(w,(json.dumps({'type':'approval_response','id':'r1','request_id':'r1:1','approved':False})+'\n').encode()); os.close(w)
    th.join(2); assert any(e['type']=='approval_resolved' and e['approved'] is False for e in events(out)); assert any(e['type']=='run_completed' for e in events(out))

def test_abort_command_denies_pending_approvals_through_stdio(tmp_path):
    r,w=os.pipe(); inp=os.fdopen(r,'r'); out=io.StringIO(); b=PiBridge(stdin=inp,stdout=out,runner_factory=lambda *a: DummyRunner(a[2],a[1]))
    th=run_stdio_in_thread(b); wait_for(out,lambda e:e['type']=='ready')
    os.write(w,(json.dumps(run_cmd(tmp_path,'approve-twice'))+'\n').encode()); wait_for(out,lambda e:e['type']=='approval_required')
    os.write(w,(json.dumps({'type':'abort','id':'r1'})+'\n').encode()); os.close(w)
    th.join(2); assert any(e['type']=='approval_resolved' and e['approved'] is False for e in events(out)); assert any(e['type']=='run_aborted' for e in events(out))

def test_git_changed_files_uses_devnull_stdin(monkeypatch, tmp_path):
    calls=[]
    class Proc:
        returncode=0; stdout='?? x.py\n'; stderr=''
    def fake_run(*args, **kwargs):
        calls.append(kwargs); return Proc()
    monkeypatch.setattr('villani_code.integrations.pi_bridge.subprocess.run', fake_run)
    assert git_changed_files(tmp_path)==['x.py']
    assert calls[0]['stdin'] is subprocess.DEVNULL
    assert calls[0]['check'] is False
    assert calls[0]['timeout']==10

def test_worker_diagnostics_order_before_runner(tmp_path):
    out=io.StringIO(); order=[]
    def factory(cmd,event_callback,approval_callback):
        order.extend([e.get('message', e['type']) for e in events(out)])
        return DummyRunner(approval_callback,event_callback)
    b=PiBridge(stdin=io.StringIO(),stdout=out,runner_factory=factory)
    b.handle(run_cmd(tmp_path))
    wait_for(out,lambda e:e['type']=='run_completed')
    assert 'run_started' in [e['type'] for e in events(out)]
    assert 'capturing initial git status' in order
    assert 'captured initial git status' in order
    assert 'creating runner' in order
    assert order.index('capturing initial git status') < order.index('captured initial git status') < order.index('creating runner')

def test_worker_continues_when_git_status_times_out(monkeypatch, tmp_path):
    def boom(_repo):
        raise subprocess.TimeoutExpired(['git'], 10)
    monkeypatch.setattr('villani_code.integrations.pi_bridge.git_changed_files', boom)
    b,out,_,_=make_bridge()
    b.handle(run_cmd(tmp_path))
    wait_for(out,lambda e:e['type']=='run_failed')
    assert any(e['type']=='run_failed' for e in events(out))

def test_approval_required_shape_and_bash_input_command(tmp_path):
    b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,'bash')); wait_for(out,lambda e:e['type']=='approval_required')
    e=[x for x in events(out) if x['type']=='approval_required'][0]
    assert isinstance(e['summary'], str)
    assert isinstance(e['input'], dict)
    assert e['summary']=='Run command'
    assert e['input']['command']=='echo hello && cat secret'
    assert e['tool']=='Bash'
    assert 'title' not in e
    b.abort('r1')

def test_map_runner_event_maps_tool_result_and_command_lifecycle():
    from villani_code.integrations.pi_bridge import map_runner_event
    finished=map_runner_event('r', {'type':'tool_result','name':'Bash','input':{'command':'echo hi'},'is_error':False,'result':{'exit_code':0,'stdout':'hello'}})
    assert finished[0]['type']=='tool_result'
    assert finished[0]['tool']=='Bash'
    assert finished[0]['ok'] is True
    assert 'Bash finished: exit 0' in finished[0]['summary']
    progress=map_runner_event('r', {'type':'command_started','command':'sleep 1','cwd':'/tmp'})
    assert progress==[{'type':'command_started','id':'r','tool':'Bash','command':'sleep 1','cwd':'/tmp'}]
    done=map_runner_event('r', {'type':'command_finished','command':'false','exit_code':1,'stderr':'bad'})
    assert done==[{'type':'command_finished','id':'r','tool':'Bash','command':'false','exit_code':1,'stdout_preview':'','stderr_preview':'bad','truncated':False}]

def test_bash_tool_result_summary_truncates_output():
    from villani_code.integrations.pi_bridge import map_runner_event
    long='x'*800
    ev=map_runner_event('r', {'type':'tool_result','name':'Bash','input':{'command':'pytest'},'result':{'exit_code':1,'stdout':long,'stderr':long}})[0]
    assert len(ev['summary']) < 1200
    assert 'output truncated' in ev['summary']


def test_nonzero_bash_events_do_not_map_to_run_failed():
    from villani_code.integrations.pi_bridge import map_runner_event
    command = map_runner_event('r', {'type':'command_finished','command':'false','exit_code':255})
    result = map_runner_event('r', {'type':'tool_result','name':'Bash','is_error':False,'result':{'exit_code':255}})
    assert all(e['type'] != 'run_failed' for e in command + result)
    assert command[0]['type'] == 'command_finished'
    assert result[0]['type'] == 'tool_result'
    assert result[0]['is_error'] is False

def test_runner_heartbeat_is_emitted_after_idle_active_run(monkeypatch):
    from villani_code.integrations import pi_bridge as mod
    b,out,_,_=make_bridge()
    ar=mod.ActiveRun(type('Cmd',(),{'id':'r-heart'})())
    ar.last_runner_event_type='tool_result'
    ar.last_runner_event_at=0
    ar.heartbeat_due_at=0
    ar.thread=type('T',(),{'is_alive':lambda self: True})()
    b._active['r-heart']=ar
    b._stdio_running=True
    monkeypatch.setattr(mod.time, 'monotonic', lambda: 16)
    b._drain_events()
    ev=events(out)[0]
    assert ev['type']=='runner_heartbeat'
    assert ev['id']=='r-heart'
    assert ev['last_event_type']=='tool_result'
    assert ev['seconds_since_last_event']==16
    assert ev['worker_alive'] is True

def test_extract_summary_reads_response_content_blocks():
    from villani_code.integrations.pi_bridge import extract_summary
    result={'response':{'content':[{'type':'text','text':'All 14 tests pass.'},{'type':'text','text':'Summary here.'}]}}
    assert extract_summary(result)=='All 14 tests pass.\n\nSummary here.'

def test_extract_summary_reads_execution_final_text():
    from villani_code.integrations.pi_bridge import extract_summary
    assert extract_summary({'execution':{'final_text':'final markdown'}})=='final markdown'

def test_extract_summary_reads_transcript_final_assistant_content():
    from villani_code.integrations.pi_bridge import extract_summary
    result={'transcript':{'final_assistant_content':[{'type':'text','text':'final assistant'}]}}
    assert extract_summary(result)=='final assistant'

def test_extract_summary_falls_back_safely_when_absent():
    from villani_code.integrations.pi_bridge import extract_summary
    assert extract_summary({'response':{'content':[{'type':'toolCall','arguments':{'x':1}}]}}) is None

def test_run_completed_includes_summary_from_final_assistant_content(tmp_path):
    class R(DummyRunner):
        def run(self, task, execution_budget=None):
            return {'response':{'content':[{'type':'text','text':'All tests pass.'}]},'execution':{'completed':True}}
    b,out,_,_=make_bridge(R())
    b.handle(run_cmd(tmp_path))
    es=wait_for(out,lambda e:e['type']=='run_completed')
    ev=[e for e in es if e['type']=='run_completed'][-1]
    assert ev['summary']=='All tests pass.'

def test_map_runner_event_maps_stream_text_field():
    from villani_code.integrations.pi_bridge import map_runner_event
    ev=map_runner_event('r', {'type':'stream_text','text':'hello human'})
    assert ev==[{'type':'stream_text','id':'r','text':'hello human'}]

def test_map_runner_event_extracts_assistant_content_text_blocks():
    from villani_code.integrations.pi_bridge import map_runner_event
    ev=map_runner_event('r', {'type':'assistant_response','content':[{'type':'text','text':'First'},{'type':'tool_use','input':{'x':1}},{'type':'text','text':'Second'}]})
    assert ev==[{'type':'stream_text','id':'r','text':'First\n\nSecond'}]

def test_map_runner_event_does_not_emit_hidden_prompts_or_tool_json_as_stream_text():
    from villani_code.integrations.pi_bridge import map_runner_event
    assert map_runner_event('r', {'type':'hidden_prompt','content':[{'type':'text','text':'secret prompt'}]})==[]
    assert map_runner_event('r', {'type':'assistant_response','content':[{'type':'tool_use','input':{'command':'pytest'}}]})==[]

def test_bridge_diagnostic_remains_diagnostic_event_for_render_suppression():
    from villani_code.integrations.pi_bridge import map_runner_event
    ev=map_runner_event('r', {'type':'model_request_started'})
    assert {'type':'bridge_diagnostic','id':'r','message':'model request started'} in ev

def test_tool_started_read_preserves_input_file_path():
    from villani_code.integrations.pi_bridge import map_runner_event
    ev=map_runner_event('r', {'type':'tool_started','name':'Read','input':{'file_path':'src/foo.py','content':'secret'}})[0]
    assert ev['type']=='tool_started'
    assert ev['input']=={'file_path':'src/foo.py'}
    assert ev['path']=='src/foo.py'


def test_tool_started_read_preserves_event_path():
    from villani_code.integrations.pi_bridge import map_runner_event
    ev=map_runner_event('r', {'type':'tool_started','name':'Read','path':'src/bar.py','input':{}})[0]
    assert ev['path']=='src/bar.py'
    assert ev['input']=={}


def test_tool_started_bash_preserves_input_command_and_command():
    from villani_code.integrations.pi_bridge import map_runner_event
    ev=map_runner_event('r', {'type':'tool_started','name':'Bash','input':{'command':'pytest -q','cwd':'/tmp','env':{'SECRET':'x'}}})[0]
    assert ev['input']=={'command':'pytest -q','cwd':'/tmp'}
    assert ev['command']=='pytest -q'


def test_tool_result_preserves_safe_input_path_and_command():
    from villani_code.integrations.pi_bridge import map_runner_event
    ev=map_runner_event('r', {'type':'tool_result','name':'Bash','input':{'command':'python -m pytest','path':'tests/test_x.py','headers':{'authorization':'x'}},'result':{'exit_code':1,'stderr':'bad'}})[0]
    assert ev['input']=={'path':'tests/test_x.py','command':'python -m pytest'}
    assert ev['path']=='tests/test_x.py'
    assert ev['command']=='python -m pytest'
    assert ev['stderr_preview']=='bad'


def test_tool_started_safe_input_excludes_sensitive_and_large_fields():
    from villani_code.integrations.pi_bridge import map_runner_event
    ev=map_runner_event('r', {'type':'tool_started','name':'Patch','input':{'path':'a.py','content':'secret file','patch':'diff --git secret','env':{'API_KEY':'x'},'headers':{'authorization':'x'},'api_key':'x','secret':'x','command':'echo ok'}})[0]
    assert ev['input']=={'path':'a.py','command':'echo ok'}
    dumped=json.dumps(ev).lower()
    for forbidden in ['content','diff --git','env','headers','api_key','secret file']:
        assert forbidden not in dumped
