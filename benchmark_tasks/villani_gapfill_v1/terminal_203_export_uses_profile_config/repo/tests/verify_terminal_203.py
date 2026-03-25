import subprocess, sys
from pathlib import Path
repo = Path(__file__).resolve().parents[1]
out = repo / "export.txt"
proc = subprocess.run([sys.executable, "-m", "app.cli", "--profile", "pipe", "--output", str(out)], cwd=repo, capture_output=True, text=True)
assert proc.returncode == 0, proc.stderr
assert out.read_text(encoding="utf-8") == "name|score\nA|1\n"
