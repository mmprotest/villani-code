use item_printer::collect_display;

#[test]
fn test_integers_display() {
    let nums = vec![1_i32, 2, 3];
    let result = collect_display(&nums);
    assert_eq!(result, vec!["1", "2", "3"]);
}

#[test]
fn test_strings_display() {
    let words = vec!["hello".to_string(), "world".to_string()];
    let result = collect_display(&words);
    assert_eq!(result, vec!["hello", "world"]);
}

#[test]
fn test_empty_slice() {
    let empty: Vec<i32> = vec![];
    let result = collect_display(&empty);
    assert!(result.is_empty());
}
