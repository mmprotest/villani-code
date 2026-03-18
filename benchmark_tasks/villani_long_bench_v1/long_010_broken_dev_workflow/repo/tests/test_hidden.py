import subprocess, sys
from pathlib import Path

def test_dev_check_outputs_mode_without_stderr_noise():
    proc = subprocess.run([sys.executable, 'scripts/run_dev_check.py'], capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stdout.strip() == 'mode=dev'
    assert proc.stderr.strip() == ''
    assert not list(Path('.').glob('*.tmp'))
