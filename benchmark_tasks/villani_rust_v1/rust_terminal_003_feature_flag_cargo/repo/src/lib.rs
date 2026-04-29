pub fn format_plain(value: &str) -> String {
    value.trim().to_string()
}

#[cfg(feature = "json_fmt")]
pub fn format_json(value: &str) -> String {
    format!("{{\"value\":\"{}\"}}", value.trim())
}

pub fn format_output(value: &str) -> String {
    #[cfg(feature = "json_fmt")]
    {
        format_json(value)
    }
    #[cfg(not(feature = "json_fmt"))]
    {
        format_plain(value)
    }
}
