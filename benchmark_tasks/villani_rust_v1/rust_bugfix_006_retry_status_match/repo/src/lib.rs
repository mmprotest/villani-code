pub fn should_retry(status: u16) -> bool {
    match status {
        400..=499 => true,
        _ => false,
    }
}
