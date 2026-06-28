from __future__ import annotations

import io, json, subprocess, tempfile, time
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

def test_stream_text_and_executionplan_approval_events_are_not_exposed(tmp_path):
    b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,'stream')); wait_for(out,lambda e:e['type']=='run_completed'); assert 'SECRET' not in out.getvalue(); assert 'approval_required' not in [e['type'] for e in events(out)]

def test_write_approval_blocks_and_rejected_write_does_not_mutate_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,'write')); wait_for(out,lambda e:e['type']=='approval_required'); assert not (tmp_path/'a.txt').exists(); b.approval(type('C',(),{'id':'r1','request_id':'r1:1','approved':False})()); wait_for(out,lambda e:e['type']=='run_completed'); assert not (tmp_path/'a.txt').exists()

def test_bash_approval_summary_is_concise(tmp_path):
    b,out,_,_=make_bridge(); b.handle(run_cmd(tmp_path,'bash')); wait_for(out,lambda e:e['type']=='approval_required'); e=[x for x in events(out) if x['type']=='approval_required'][0]; assert e['title']=='Run command'; assert e['summary']=={'command':'echo hello && cat secret'}; b.abort('r1')

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
