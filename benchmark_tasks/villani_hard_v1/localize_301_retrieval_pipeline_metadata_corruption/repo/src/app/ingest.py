from __future__ import annotations

def chunk_documents(documents: list[dict]) -> list[dict]:
    chunks = []
    shared_meta = {}
    for doc in documents:
        shared_meta['topic'] = doc['topic']
        shared_meta['source_id'] = doc['id']
        chunks.append({'text': doc['text'], 'meta': shared_meta})
    return chunks
