import subprocess, sys

def run_cli(*args):
    return subprocess.run([sys.executable, '-m', 'app.cli', *args], capture_output=True, text=True)

def test_stats_command_succeeds():
    out = run_cli('stats', '1', '2', '3')
    assert out.returncode == 0
    assert out.stdout.strip() == 'count=3 total=6 average=2.0'

def test_stats_help_text_exists():
    out = run_cli('--help')
    assert 'stats' in out.stdout

def test_stats_invalid_input_nonzero_and_stderr():
    out = run_cli('stats')
    assert out.returncode != 0
    assert 'no values' in out.stderr.lower() or 'usage:' in out.stderr.lower()
