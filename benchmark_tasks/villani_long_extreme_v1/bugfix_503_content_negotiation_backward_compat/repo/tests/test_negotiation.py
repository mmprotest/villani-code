from app.api import respond
ITEM={"id":"p1","name":"alpha","source":"db","score":0.9}
def test_legacy_accept_header_keeps_v1_shape(): assert respond(ITEM,"application/json")=={"id":"p1","name":"alpha"}
def test_vendor_v2_gets_enriched_shape(): assert respond(ITEM,"application/vnd.example.v2+json")["meta"]=={"source":"db","score":0.9}
def test_wildcard_prefers_backward_compatible_default(): assert respond(ITEM,"*/*")=={"id":"p1","name":"alpha"}
