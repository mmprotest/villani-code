use dedup_util::unique_items;

#[test]
fn test_non_consecutive_duplicates_are_removed() {
    let input = vec![
        "a".to_string(),
        "b".to_string(),
        "a".to_string(),
        "c".to_string(),
        "b".to_string(),
    ];
    let result = unique_items(input);
    assert_eq!(
        result,
        vec!["a".to_string(), "b".to_string(), "c".to_string()],
        "unique_items should remove all duplicates, preserving first-occurrence order"
    );
}

#[test]
fn test_already_unique_list_is_unchanged() {
    let input = vec!["x".to_string(), "y".to_string(), "z".to_string()];
    let result = unique_items(input.clone());
    assert_eq!(result, input, "a list with no duplicates should be returned unchanged");
}

#[test]
fn test_all_same_elements_returns_single() {
    let input = vec!["dup".to_string(), "dup".to_string(), "dup".to_string()];
    let result = unique_items(input);
    assert_eq!(result, vec!["dup".to_string()], "all identical elements should collapse to one");
}
