use feature_flag_cargo::format_output;

#[test]
fn test_format_output_plain_always_works() {
    #[cfg(not(feature = "json_output"))]
    {
        let result = format_output("  hello  ");
        assert_eq!(result, "hello", "plain output should trim whitespace");
    }
    #[cfg(feature = "json_output")]
    {
        let result = format_output("  hello  ");
        assert!(
            result.contains("hello"),
            "output should contain the value"
        );
    }
}

#[test]
fn test_format_output_produces_json_with_default_features() {
    let result = format_output("  hello  ");
    assert_eq!(
        result,
        "{\"value\":\"hello\"}",
        "default features should enable json_output; got plain text instead — \
         check the [features] default list in Cargo.toml"
    );
}
