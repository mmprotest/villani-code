import subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_e2e_script():
    result = subprocess.run([sys.executable, str(ROOT/'scripts'/'run_e2e.py')], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
