class ProjectionStore:
    def __init__(self): self.rows={}; self.applied=set()
    def has_row(self,a): return a in self.rows
    def get_balance(self,a): return self.rows.get(a,0)
    def set_balance(self,a,v): self.rows[a]=v
    def mark_applied(self, seq): self.applied.add(seq)
    def was_applied(self, seq): return seq in self.applied
