pub mod errors;

use errors::AppError;

pub fn load_value(line: &str) -> Result<i64, AppError> {
    let parts: Vec<&str> = line.splitn(2, '=').collect();
    if parts.len() != 2 {
        return Err(AppError::NotFound(line.to_string()));
    }
    let value_str = parts[1].trim();
    let value: i64 = value_str.parse().map_err(|_| AppError::Generic)?;
    Ok(value)
}

pub fn load_sum(lines: &[&str]) -> Result<i64, AppError> {
    let mut total = 0i64;
    for line in lines {
        total += load_value(line)?;
    }
    Ok(total)
}
