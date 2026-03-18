from app.net import RetryableError
from app.workflow import execute

def test_retry_then_success():
    state = {'n': 0}
    def fn():
        state['n'] += 1
        if state['n'] < 2:
            raise RetryableError('try again')
    assert execute(fn) == 0
