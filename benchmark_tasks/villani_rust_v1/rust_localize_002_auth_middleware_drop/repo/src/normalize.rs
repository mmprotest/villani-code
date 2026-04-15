use std::collections::HashMap;

pub fn normalize_headers(headers: HashMap<String, String>) -> HashMap<String, String> {
    headers
        .into_iter()
        .map(|(k, v)| (k.to_lowercase(), v))
        .collect()
}
