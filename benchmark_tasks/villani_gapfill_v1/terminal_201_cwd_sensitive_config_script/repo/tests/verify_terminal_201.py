import subprocess, sys
from pathlib import Path
repo = Path(__file__).resolve().parents[1]
workdir = repo / "tests"
proc = subprocess.run([sys.executable, str(repo / "scripts" / "render_report.py")], cwd=workdir, capture_output=True, text=True)
assert proc.returncode == 0, proc.stderr
assert "Monthly Summary" in proc.stdout
