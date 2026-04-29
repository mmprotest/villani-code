use retry_policy::should_retry;

#[test]
fn server_error_500_should_be_retried() {
    assert!(should_retry(500), "500 Internal Server Error should be retried");
}

#[test]
fn server_error_503_should_be_retried() {
    assert!(should_retry(503), "503 Service Unavailable should be retried");
}

#[test]
fn client_error_400_should_not_be_retried() {
    assert!(!should_retry(400), "400 Bad Request should not be retried");
}

#[test]
fn client_error_404_should_not_be_retried() {
    assert!(!should_retry(404), "404 Not Found should not be retried");
}

#[test]
fn rate_limit_429_should_be_retried() {
    assert!(should_retry(429), "429 Too Many Requests should be retried");
}

#[test]
fn success_200_should_not_be_retried() {
    assert!(!should_retry(200), "200 OK should not be retried");
}
