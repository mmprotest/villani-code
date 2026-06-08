use module_structure::{greet, add};

#[test]
fn test_greet_returns_hello_message() {
    let result = greet("Alice");
    assert_eq!(result, "Hello, Alice!");
}

#[test]
fn test_greet_world() {
    let result = greet("world");
    assert_eq!(result, "Hello, world!");
}

#[test]
fn test_add_positive_numbers() {
    assert_eq!(add(1, 2), 3);
}

#[test]
fn test_add_with_zero() {
    assert_eq!(add(0, 5), 5);
    assert_eq!(add(7, 0), 7);
}

#[test]
fn test_add_negative_numbers() {
    assert_eq!(add(-3, 3), 0);
    assert_eq!(add(-1, -1), -2);
}
