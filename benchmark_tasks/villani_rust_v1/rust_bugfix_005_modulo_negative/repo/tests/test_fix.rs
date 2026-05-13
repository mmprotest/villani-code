use wrap_index::wrap_index;

#[test]
fn test_negative_one_wraps_to_last() {
    assert_eq!(
        wrap_index(-1, 5),
        4,
        "wrap_index(-1, 5) should be 4, not -1"
    );
}

#[test]
fn test_negative_large_wraps_correctly() {
    assert_eq!(
        wrap_index(-7, 5),
        3,
        "wrap_index(-7, 5) should be 3"
    );
}

#[test]
fn test_positive_index_unchanged_when_in_range() {
    assert_eq!(
        wrap_index(3, 5),
        3,
        "wrap_index(3, 5) should remain 3"
    );
}

#[test]
fn test_positive_index_wraps_when_over_len() {
    assert_eq!(
        wrap_index(7, 5),
        2,
        "wrap_index(7, 5) should wrap to 2"
    );
}

#[test]
fn test_zero_index_is_zero() {
    assert_eq!(
        wrap_index(0, 5),
        0,
        "wrap_index(0, 5) should be 0"
    );
}
