def filter_chunks(chunks: list[dict], topic: str | None = None) -> list[dict]:
    if topic is None:
        return list(chunks)
    return [c for c in chunks if c['meta'].get('topic') == topic]
