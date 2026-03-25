from .builder import RequestBuilder

def build_headers(token=None):
    extra = {"Authorization": f"Bearer {token}"} if token else None
    return RequestBuilder().build(extra)
