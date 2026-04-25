import sys
from pathlib import Path
from app.worker import Worker
ITEMS=["alpha","beta","gamma"]
def main():
    state_path=Path(sys.argv[1]); mode=sys.argv[2]; w=Worker(state_path,ITEMS)
    if mode=="once": w.step(); return
    if mode=="until_done":
        while w.step(): pass
        return
    raise SystemExit("bad mode")
if __name__=="__main__": main()
