from .compiler import Compiler
from .graph import build_dependency_graph, reverse_graph

class BuildSystem:
    def __init__(self): self.compiler=Compiler(); self.cache={}; self.graph={}; self.rev={}
    def load(self, files): self.files=dict(files); self.graph=build_dependency_graph(self.files); self.rev=reverse_graph(self.graph); self.cache={}
    def build_all(self):
        outputs={}
        for name in self.files: outputs[name]=self._build_one(name, outputs)
        return outputs
    def _build_one(self, name, outputs):
        if name in self.cache: return self.cache[name]
        deps={dep: self._build_one(dep, outputs) for dep in self.graph.get(name, set())}
        out=self.compiler.compile(name, self.files[name], deps)
        self.cache[name]=out; outputs[name]=out; return out
    def update_file(self, name, new_source):
        self.files[name]=new_source; self.graph=build_dependency_graph(self.files); self.rev=reverse_graph(self.graph); self.invalidate(name)
    def invalidate(self, changed):
        self.cache.pop(changed, None)
        for dep in self.rev.get(changed, set()): self.cache.pop(dep, None)
