from .middleware import apply_middleware
from .request_builder import build_headers

def request(token: str | None) -> dict[str, str]:
    return apply_middleware(build_headers(token))
