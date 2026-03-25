from app.registry import build_registry

def test_alias_chain_is_preserved_in_registry():
    reg = build_registry()
    assert reg["st"] == "status"
    assert reg["stat"] == "status"
