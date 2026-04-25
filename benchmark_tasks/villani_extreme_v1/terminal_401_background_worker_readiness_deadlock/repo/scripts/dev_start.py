import threading
from app.api import run_api
from app.bootstrap import warm_up
from app.worker import start_worker

def main():
    start_worker(); threading.Thread(target=warm_up, daemon=True).start(); run_api()
if __name__ == "__main__": main()
