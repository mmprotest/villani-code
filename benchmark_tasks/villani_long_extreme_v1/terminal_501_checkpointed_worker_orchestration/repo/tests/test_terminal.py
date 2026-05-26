import json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run(*args): return subprocess.run([sys.executable,*args],cwd=ROOT,capture_output=True,text=True,check=True)
def test_resume_and_repeat_runs_are_idempotent():
    with tempfile.TemporaryDirectory() as td:
        state=Path(td)/"state.json"; run("scripts/run_batch.py",str(state),"once"); run("scripts/run_batch.py",str(state),"until_done"); data=json.loads(state.read_text()); assert data=={"cursor":3,"done":["ALPHA","BETA","GAMMA"]}; run("scripts/run_batch.py",str(state),"until_done"); assert json.loads(state.read_text())==data
