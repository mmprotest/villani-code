use input_validator::validate;

#[test]
fn test_empty_input_is_invalid() {
    let result = validate("");
    assert!(
        result.is_err(),
        "empty input should be invalid, but validate returned Ok"
    );
}

#[test]
fn test_valid_alphanumeric_input_is_ok() {
    let result = validate("hello123");
    assert!(
        result.is_ok(),
        "alphanumeric input should be valid, but validate returned Err: {:?}",
        result
    );
}

#[test]
fn test_input_with_special_chars_is_invalid() {
    let result = validate("bad input!");
    assert!(
        result.is_err(),
        "input with spaces and punctuation should be invalid"
    );
}
