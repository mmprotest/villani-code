from app.retrieval import query
from app.loader import ProfileLoader
def test_guides_profile_does_not_drop_public_docs():
    loader=ProfileLoader(); loader.load("internal_only"); assert query("guides")==["g1","p1"]
def test_internal_profile_keeps_internal_note():
    assert query("internal_only")==["g1","p1","n1"]
def test_profile_loading_is_stable_across_orderings():
    loader=ProfileLoader(); a=loader.load("guides"); b=loader.load("internal_only"); c=loader.load("guides"); assert c==a and b!=a
