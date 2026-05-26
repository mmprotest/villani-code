class CheckpointStore:
    def __init__(self): self._seq=0
    def save(self, seq:int):
        if seq>self._seq: self._seq=seq
    def load(self)->int: return self._seq
