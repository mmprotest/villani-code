class Graph:
    def __init__(self,deps):
        self.deps=deps; self.reverse={}
        for node,refs in deps.items():
            self.reverse.setdefault(node,set())
            for ref in refs: self.reverse.setdefault(ref,set()).add(node)
    def direct_dependents(self,node): return set(self.reverse.get(node,set()))
