use std::collections::HashMap;

use auth_middleware::client::{process_request, Request};

fn make_request_with_auth(token: &str) -> Request {
    let mut headers = HashMap::new();
    headers.insert("Authorization".to_string(), token.to_string());
    headers.insert("Content-Type".to_string(), "application/json".to_string());
    Request::new(headers, r#"{"action":"read"}"#)
}

#[test]
fn test_authenticated_request_returns_200() {
    let req = make_request_with_auth("Bearer valid-token-abc123");
    let resp = process_request(req);
    assert_eq!(
        resp.status, 200,
        "a request with a valid Authorization header should return 200, got {}; body: {}",
        resp.status, resp.body
    );
}

#[test]
fn test_unauthenticated_request_returns_401() {
    let headers = HashMap::new();
    let req = Request::new(headers, "{}");
    let resp = process_request(req);
    assert_eq!(
        resp.status, 401,
        "a request with no Authorization header should return 401"
    );
}

#[test]
fn test_empty_token_returns_401() {
    let req = make_request_with_auth("");
    let resp = process_request(req);
    assert_eq!(
        resp.status, 401,
        "a request with an empty Authorization token should return 401"
    );
}

#[test]
fn test_mixed_case_header_key_is_accepted() {
    let mut headers = HashMap::new();
    headers.insert("authorization".to_string(), "Bearer another-token".to_string());
    let req = Request::new(headers, "{}");
    let resp = process_request(req);
    assert_eq!(
        resp.status, 200,
        "a request with lowercase 'authorization' header should return 200"
    );
}
