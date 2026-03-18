from app.net import FatalError, RetryableError
from app.retry import run_with_retry
from app.workflow import execute

def test_non_retryable_error_does_not_retry():
    state = {'n': 0}
    def fn():
        state['n'] += 1
        raise FatalError('bad')
    assert execute(fn) != 0
    assert state['n'] == 1

def test_final_retryable_failure_propagates_nonzero():
    state = {'n': 0}
    def fn():
        state['n'] += 1
        raise RetryableError('still bad')
    assert execute(fn) != 0
    assert state['n'] == 3

def test_run_with_retry_returns_value_on_success():
    assert run_with_retry(lambda: 5) == 5
