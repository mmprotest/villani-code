def build_scope_map(module):
    scopes={"module":{}}
    for stmt in module.statements:
        name=stmt.__class__.__name__
        if name=="ImportAlias": scopes["module"][stmt.alias] = ("import", stmt.target)
        elif name=="Assignment": scopes["module"][stmt.target] = ("local", stmt.source)
        elif name=="FunctionDef":
            scopes[stmt.name]={}
            for inner in stmt.body:
                inner_name=inner.__class__.__name__
                if inner_name=="ImportAlias": scopes[stmt.name][inner.alias] = ("import", inner.target)
                elif inner_name=="Assignment": scopes[stmt.name][inner.target] = ("local", inner.source)
    return scopes
