import json, subprocess, sys, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def get_json(path):
    with urllib.request.urlopen(f"http://127.0.0.1:8123{path}", timeout=1.0) as resp: return json.loads(resp.read().decode())

def post_json(path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:8123{path}", method="POST", headers={"Content-Type": "application/json"}, data=json.dumps(payload).encode())
    with urllib.request.urlopen(req, timeout=1.0) as resp: return resp.status

def main():
    proc = subprocess.Popen([sys.executable, str(ROOT/'scripts'/'dev_start.py')], cwd=str(ROOT))
    try:
        for _ in range(30):
            try:
                get_json('/health')
                break
            except Exception:
                time.sleep(0.05)
        post_json('/seed', {'jobs':['alpha','beta']})
        for _ in range(20):
            jobs = get_json('/jobs')
            if jobs['processed'] == ['ALPHA','BETA']:
                print('ok'); return 0
            time.sleep(0.05)
        print('processed mismatch', jobs); return 1
    finally:
        proc.terminate(); proc.wait(timeout=3)
if __name__ == '__main__': raise SystemExit(main())
