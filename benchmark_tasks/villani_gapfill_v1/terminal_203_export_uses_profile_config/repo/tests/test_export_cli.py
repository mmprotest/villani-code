import subprocess, sys
from pathlib import Path

def test_export_uses_selected_profile():
    repo = Path(__file__).resolve().parents[1]
    out = repo / "export_hidden.txt"
    subprocess.run([sys.executable, "-m", "app.cli", "--profile", "pipe", "--output", str(out)], cwd=repo, check=True)
    assert out.read_text(encoding="utf-8") == "name|score\nA|1\n"

def test_export_default_profile_still_uses_comma():
    repo = Path(__file__).resolve().parents[1]
    out = repo / "export_default.txt"
    subprocess.run([sys.executable, "-m", "app.cli", "--output", str(out)], cwd=repo, check=True)
    assert out.read_text(encoding="utf-8") == "name,score\nA,1\n"
