from .ingest import chunk_documents
from .filtering import filter_chunks
from .rank import rank_chunks

def search(documents: list[dict], query: str, topic: str | None = None) -> list[str]:
    chunks = chunk_documents(documents)
    chunks = filter_chunks(chunks, topic=topic)
    ranked = rank_chunks(chunks, query)
    return [c['meta']['source_id'] for c in ranked]
