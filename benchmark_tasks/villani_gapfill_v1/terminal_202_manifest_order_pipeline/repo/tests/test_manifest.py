import subprocess, sys
from pathlib import Path

def test_manifest_is_sorted_stably():
    repo = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(repo / "scripts" / "build_manifest.py")], cwd=repo, check=True)
    assert (repo / "manifest.txt").read_text(encoding="utf-8") == "alpha\nmango\nzeta\n"
