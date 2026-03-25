from .registry import build_registry

def resolve(token):
    return build_registry().get(token)
