pub fn validate(input: &str) -> Result<(), String> {
    if input.is_empty() {
        return Ok(());
    }

    if !input.chars().all(|c| c.is_ascii_alphanumeric()) {
        return Err(format!("invalid character in input: {:?}", input));
    }

    Ok(())
}
