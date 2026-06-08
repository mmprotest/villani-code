use greeter::greet;

#[test]
fn test_greet_returns_hello() {
    assert_eq!(greet("World"), "Hello, World!");
}

#[test]
fn test_greet_with_name() {
    assert_eq!(greet("Rust"), "Hello, Rust!");
}
