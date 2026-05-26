from .loader import ProfileLoader
DOCS=[{"id":"g1","kind":"guide","visibility":"public"},{"id":"p1","kind":"policy","visibility":"public"},{"id":"n1","kind":"note","visibility":"internal"}]
def query(profile_name):
    profile=ProfileLoader().load(profile_name)
    return [d["id"] for d in DOCS if d["kind"] in profile["kinds"] and d["visibility"] in profile["visibility"]]
