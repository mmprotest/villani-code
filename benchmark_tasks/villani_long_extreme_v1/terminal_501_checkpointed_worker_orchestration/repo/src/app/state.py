import json
from pathlib import Path
class StateStore:
    def __init__(self,path): self.path=Path(path)
    def load(self):
        if not self.path.exists(): return {"cursor":0,"done":[]}
        return json.loads(self.path.read_text())
    def save(self,state): self.path.write_text(json.dumps(state))
