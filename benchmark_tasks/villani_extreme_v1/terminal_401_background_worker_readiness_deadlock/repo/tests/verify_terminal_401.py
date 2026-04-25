import subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
raise SystemExit(subprocess.run([sys.executable, str(ROOT/'scripts'/'run_e2e.py')], cwd=ROOT).returncode)
