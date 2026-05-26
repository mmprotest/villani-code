from app.retrieval import retrieve
DOCS=[{"id":"g1","kind":"guide","text":"Install guide for the product"},{"id":"f1","kind":"faq","text":"Frequently asked questions"},{"id":"n1","kind":"notes","text":"Internal notes"},{"id":"s1","section":"guide","text":"Legacy guide representation"}]

def test_docs_only_profile_keeps_kind_based_guides(): assert retrieve(DOCS, "install", profile="docs_only")[0] == "g1"

def test_docs_only_profile_excludes_faq(): assert "f1" not in retrieve(DOCS, "questions", profile="docs_only")

def test_default_profile_still_accepts_section_based_docs(): assert retrieve(DOCS, "legacy")[0] == "s1"
