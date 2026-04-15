use string_util::truncate;

#[test]
fn ascii_string_truncates_correctly() {
    assert_eq!(truncate("hello world", 5), "hello");
}

#[test]
fn ascii_string_shorter_than_limit_is_unchanged() {
    assert_eq!(truncate("hi", 10), "hi");
}

#[test]
fn emoji_string_does_not_panic() {
    let s = "Hello 🌍🌎🌏";
    let result = truncate(s, 7);
    assert_eq!(result, "Hello 🌍");
}

#[test]
fn multibyte_characters_counted_by_char_not_byte() {
    let s = "café!";
    let result = truncate(s, 4);
    assert_eq!(result, "café");
}

#[test]
fn cjk_characters_do_not_panic() {
    let s = "你好世界";
    let result = truncate(s, 2);
    assert_eq!(result, "你好");
}
