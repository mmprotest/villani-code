from .serializer import serialize_v1, serialize_v2
V2="application/vnd.example.v2+json"
V1="application/json"
def choose_serializer(accept_header:str):
    header=(accept_header or "").lower()
    # BUG: any mention of json routes to v2, breaking legacy clients and wildcard fallback.
    if "json" in header: return serialize_v2
    if V2 in header: return serialize_v2
    return serialize_v1
