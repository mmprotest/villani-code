use std::collections::HashMap;

pub fn load_config(key: &str, file_config: &HashMap<String, String>) -> Option<String> {
    if let Some(val) = file_config.get(key) {
        return Some(val.clone());
    }

    std::env::var(key).ok()
}
