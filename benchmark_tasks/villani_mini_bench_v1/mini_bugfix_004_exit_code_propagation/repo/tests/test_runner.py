from app.runner import run_check
import sys


def test_nonzero_exit_code_is_propagated():
    code = run_check([sys.executable, '-c', 'import sys; print("boom"); sys.exit(7)'])
    assert code == 7


def test_zero_exit_code_still_returns_zero():
    code = run_check([sys.executable, '-c', 'print("ok")'])
    assert code == 0
