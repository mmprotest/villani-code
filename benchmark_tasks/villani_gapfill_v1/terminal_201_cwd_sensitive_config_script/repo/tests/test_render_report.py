import subprocess, sys
from pathlib import Path

def test_script_outputs_title_from_repo_root():
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, str(repo / "scripts" / "render_report.py")], cwd=repo, capture_output=True, text=True)
    assert proc.returncode == 0
    assert "Monthly Summary" in proc.stdout

def test_script_also_works_from_nested_cwd():
    repo = Path(__file__).resolve().parents[1]
    workdir = repo / "tests"
    proc = subprocess.run([sys.executable, str(repo / "scripts" / "render_report.py")], cwd=workdir, capture_output=True, text=True)
    assert proc.returncode == 0
    assert "Monthly Summary" in proc.stdout
