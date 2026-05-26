import json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run(*args): return subprocess.run([sys.executable,*args],cwd=ROOT,capture_output=True,text=True,check=True)
def main():
    with tempfile.TemporaryDirectory() as td:
        state=Path(td)/"state.json"; run("scripts/run_batch.py",str(state),"once"); run("scripts/run_batch.py",str(state),"until_done"); data=json.loads(state.read_text()); assert data["done"]==["ALPHA","BETA","GAMMA"],data; assert data["cursor"]==3,data; run("scripts/run_batch.py",str(state),"until_done"); data2=json.loads(state.read_text()); assert data2==data,(data,data2)
if __name__=="__main__": main()
