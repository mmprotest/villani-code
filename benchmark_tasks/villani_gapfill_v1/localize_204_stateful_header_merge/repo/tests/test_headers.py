from app.http.builder import RequestBuilder

def test_request_specific_headers_do_not_leak():
    builder = RequestBuilder()
    first = builder.build({"Authorization": "Bearer one"})
    second = builder.build()
    assert first["Authorization"] == "Bearer one"
    assert "Authorization" not in second

def test_default_headers_are_present():
    assert RequestBuilder().build()["Accept"] == "application/json"
