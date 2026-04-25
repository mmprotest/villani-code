from app.pipeline import search
DOCS = [
    {'id': 'legal-1', 'topic': 'legal', 'text': 'tenant privacy obligations and notice periods'},
    {'id': 'ml-1', 'topic': 'ml', 'text': 'ranking models and retrieval evaluation basics'},
    {'id': 'legal-2', 'topic': 'legal', 'text': 'privacy collection consent and disclosure rules'},
]
def test_topic_filtered_search_returns_expected_doc():
    result = search(DOCS, query='privacy consent', topic='legal')
    assert result[:2] == ['legal-2', 'legal-1']
    assert len(set(result[:2])) == 2
