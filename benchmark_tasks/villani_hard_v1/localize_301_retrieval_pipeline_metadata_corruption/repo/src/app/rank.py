def rank_chunks(chunks: list[dict], query: str) -> list[dict]:
    terms = set(query.lower().split())
    scored = []
    for chunk in chunks:
        score = sum(1 for term in terms if term in chunk['text'].lower())
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for score, chunk in scored if score >= 0]
