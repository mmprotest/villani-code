from .parser import parse_deps

def build_dependency_graph(files): return {name: set(parse_deps(content)) for name, content in files.items()}
def reverse_graph(graph):
    rev={name:set() for name in graph}
    for node, deps in graph.items():
        for dep in deps: rev.setdefault(dep,set()).add(node)
    return rev
