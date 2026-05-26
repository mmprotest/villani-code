from .planner import RebuildPlanner
class BuildSystem:
    def __init__(self, graph, kinds): self.planner=RebuildPlanner(graph,kinds); self.built=[]
    def rebuild(self, changed): impacted=self.planner.affected(changed); order=sorted(impacted); self.built.extend(order); return order
