from app.ingest import chunk_documents
from app.pipeline import search
DOCS = [
    {'id': 'a', 'topic': 'finance', 'text': 'expense budgets and burn rates'},
    {'id': 'b', 'topic': 'privacy', 'text': 'privacy policy retention and consent'},
    {'id': 'c', 'topic': 'privacy', 'text': 'consent collection notices and storage limits'},
]
def test_chunk_metadata_is_not_shared_between_items():
    chunks = chunk_documents(DOCS)
    assert chunks[0]['meta'] is not chunks[1]['meta']
    assert chunks[0]['meta']['topic'] == 'finance'
    assert chunks[1]['meta']['topic'] == 'privacy'

def test_unfiltered_search_preserves_source_ids():
    result = search(DOCS, query='consent privacy')
    assert set(result[:2]) == {'b', 'c'}
