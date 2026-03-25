import subprocess, sys
from pathlib import Path
repo = Path(__file__).resolve().parents[1]
proc = subprocess.run([sys.executable, str(repo / "scripts" / "build_manifest.py")], cwd=repo, capture_output=True, text=True)
assert proc.returncode == 0, proc.stderr
manifest = (repo / "manifest.txt").read_text(encoding="utf-8")
assert manifest.splitlines() == ["alpha", "mango", "zeta"]
