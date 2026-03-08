def build_headers(token: str | None) -> dict[str, str]:
    h = {'X-Trace': '1'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    return h
