from .astish import Scope, Ref, Assign
class Resolver:
    def __init__(self): self.stack=[Scope()]
    def push(self): self.stack.append(Scope())
    def pop(self): self.stack.pop()
    def bind_alias(self, name, target): self.stack[-1].alias_bindings[name]=target
    def bind_value(self, name, value): self.stack[-1].value_bindings[name]=value
    def resolve(self, ref:Ref):
        for scope in reversed(self.stack):
            # BUG: value bindings wrongly shadow aliases for reference resolution.
            if ref.name in scope.value_bindings: return {"origin":"value","target":scope.value_bindings[ref.name]}
            if ref.name in scope.alias_bindings: return {"origin":"alias","target":scope.alias_bindings[ref.name]}
        raise KeyError(ref.name)
