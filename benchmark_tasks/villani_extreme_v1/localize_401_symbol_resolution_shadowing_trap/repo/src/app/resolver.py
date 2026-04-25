from .symbols import build_scope_map

def resolve_name(module, function_name, symbol):
    scopes=build_scope_map(module); local_scope=scopes.get(function_name, {}); module_scope=scopes["module"]
    if symbol in local_scope:
        kind, value = local_scope[symbol]
        if kind == "import": return value
    if symbol in module_scope:
        kind, value = module_scope[symbol]
        if kind == "import": return value
        return f"<local:{symbol}={value}>"
    if symbol in local_scope:
        kind, value = local_scope[symbol]
        return f"<local:{symbol}={value}>"
    return "<unresolved>"
