import subprocess, sys

def test_dev_check_script_runs():
    proc = subprocess.run([sys.executable, 'scripts/run_dev_check.py'], capture_output=True, text=True)
    assert proc.returncode == 0
