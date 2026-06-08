use std::collections::HashMap;

pub fn check_auth(headers: &HashMap<String, String>) -> Result<(), String> {
    match headers.get("Authorization") {
        Some(token) if !token.is_empty() => Ok(()),
        Some(_) => Err("Authorization token is empty".to_string()),
        None => Err("Missing Authorization header".to_string()),
    }
}
