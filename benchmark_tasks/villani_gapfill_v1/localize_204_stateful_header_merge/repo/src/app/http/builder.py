from .defaults import DEFAULT_HEADERS

class RequestBuilder:
    def __init__(self):
        self._base = DEFAULT_HEADERS

    def build(self, extra=None):
        headers = self._base
        if extra:
            headers.update(extra)
        return headers
