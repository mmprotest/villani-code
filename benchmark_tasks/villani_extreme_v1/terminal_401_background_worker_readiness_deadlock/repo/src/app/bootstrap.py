import time
from .api import state

def warm_up():
    time.sleep(0.4)
    state.ready = True
