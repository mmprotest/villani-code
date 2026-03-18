from app.a import normalize_a

def test_basic_normalization():
    assert normalize_a('  Hello World  ') == 'hello-world'
