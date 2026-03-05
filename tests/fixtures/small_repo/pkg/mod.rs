pub fn parse_value(input: &str) -> i32 {
    input.parse::<i32>().unwrap_or(0)
}
