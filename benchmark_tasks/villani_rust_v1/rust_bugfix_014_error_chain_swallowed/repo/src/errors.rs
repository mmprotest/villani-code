use std::fmt;

#[derive(Debug)]
pub enum AppError {
    Generic,
    ParseError(String),
    NotFound(String),
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::Generic => write!(f, "generic error"),
            AppError::ParseError(msg) => write!(f, "parse error: {}", msg),
            AppError::NotFound(key) => write!(f, "not found: {}", key),
        }
    }
}

impl std::error::Error for AppError {}
