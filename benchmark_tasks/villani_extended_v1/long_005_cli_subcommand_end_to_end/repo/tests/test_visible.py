import subprocess, sys

def run_cli(*args):
    return subprocess.run([sys.executable, '-m', 'app.cli', *args], capture_output=True, text=True)

def test_echo_still_works():
    out = run_cli('echo', 'a', 'b')
    assert out.returncode == 0
    assert out.stdout.strip() == 'a b'
