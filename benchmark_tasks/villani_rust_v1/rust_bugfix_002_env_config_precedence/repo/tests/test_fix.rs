use std::collections::HashMap;
use config_loader::load_config;

#[test]
fn test_env_var_wins_over_file_config() {
    let key = "CFG_TEST_LOG_LEVEL";
    std::env::set_var(key, "debug");

    let mut file_config = HashMap::new();
    file_config.insert(key.to_string(), "info".to_string());

    let result = load_config(key, &file_config);
    std::env::remove_var(key);

    assert_eq!(
        result,
        Some("debug".to_string()),
        "environment variable should take precedence over file config"
    );
}

#[test]
fn test_file_config_used_when_no_env_var() {
    let key = "CFG_TEST_OUTPUT_DIR";
    std::env::remove_var(key);

    let mut file_config = HashMap::new();
    file_config.insert(key.to_string(), "/tmp/output".to_string());

    let result = load_config(key, &file_config);
    assert_eq!(
        result,
        Some("/tmp/output".to_string()),
        "file config should be used when no environment variable is set"
    );
}

#[test]
fn test_returns_none_when_neither_source_has_key() {
    let key = "CFG_TEST_NONEXISTENT_KEY_XYZ";
    std::env::remove_var(key);
    let file_config = HashMap::new();

    let result = load_config(key, &file_config);
    assert_eq!(result, None, "should return None when key is absent everywhere");
}
