use data_loader::{load_value, load_sum};

#[test]
fn test_valid_line_parses() {
    assert_eq!(load_value("count=42").unwrap(), 42);
}

#[test]
fn test_parse_error_contains_original_cause() {
    let err = load_value("count=not_a_number").unwrap_err();
    let msg = err.to_string();
    assert!(
        !msg.contains("generic error"),
        "Expected a meaningful parse error, got: {}",
        msg
    );
    assert!(
        msg.contains("parse") || msg.contains("invalid") || msg.contains("not_a_number"),
        "Error message should describe the parse failure, got: {}",
        msg
    );
}

#[test]
fn test_sum_valid_lines() {
    let lines = vec!["a=10", "b=20", "c=30"];
    assert_eq!(load_sum(&lines).unwrap(), 60);
}

#[test]
fn test_sum_propagates_meaningful_error() {
    let lines = vec!["a=10", "b=bad_value", "c=30"];
    let err = load_sum(&lines).unwrap_err();
    let msg = err.to_string();
    assert!(
        !msg.contains("generic error"),
        "Sum should propagate meaningful error, got: {}",
        msg
    );
}
