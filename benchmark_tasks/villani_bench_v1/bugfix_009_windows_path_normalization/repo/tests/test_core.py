from app.core import normalize_path

def test_path_norm():
    assert normalize_path("a\\b") == "a/b"
