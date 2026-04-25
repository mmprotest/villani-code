from __future__ import annotations
import json, subprocess, sys, urllib.request

def run_smoke(port: int = 8765) -> dict:
    cmd = [sys.executable, '-m', 'app.server', str(port)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    proc.communicate(timeout=1)
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
        return json.loads(response.read().decode('utf-8'))

if __name__ == '__main__':
    print(run_smoke())
