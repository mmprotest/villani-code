import time
from .state import StateStore
class Worker:
    def __init__(self,state_path,items): self.store=StateStore(state_path); self.items=list(items)
    def step(self):
        state=self.store.load(); cursor=state["cursor"]
        if cursor>=len(self.items): return False
        item=self.items[cursor]; done=list(state["done"]); done.append(item.upper())
        # BUG: saves output but forgets to advance cursor before interruption/restart.
        self.store.save({"cursor":cursor,"done":done}); time.sleep(0.02); return True
