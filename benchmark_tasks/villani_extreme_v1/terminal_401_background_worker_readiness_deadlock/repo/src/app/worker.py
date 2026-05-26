import threading, time
from .api import state

def start_worker():
    def loop():
        while True:
            if state.jobs and state.ready: state.processed.append(state.jobs.pop(0).upper())
            time.sleep(0.02)
    threading.Thread(target=loop, daemon=True).start()
