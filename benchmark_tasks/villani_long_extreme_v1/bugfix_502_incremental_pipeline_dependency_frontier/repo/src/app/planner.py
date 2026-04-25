from collections import deque
class RebuildPlanner:
    def __init__(self, graph, kinds): self.graph=graph; self.kinds=kinds
    def affected(self, changed_nodes):
        q=deque(changed_nodes); seen=set(changed_nodes); impacted=set(changed_nodes)
        while q:
            node=q.popleft()
            for dep in self.graph.direct_dependents(node):
                if dep in seen: continue
                seen.add(dep)
                # BUG: this prunes traversal through analysis nodes and misses runtime consumers behind them.
                if self.kinds.get(dep)=="analysis": continue
                impacted.add(dep); q.append(dep)
        return impacted
