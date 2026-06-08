use std::collections::HashMap;

use crate::auth::check_auth;
use crate::normalize::normalize_headers;

#[derive(Debug, Clone)]
pub struct Request {
    pub headers: HashMap<String, String>,
    pub body: String,
}

impl Request {
    pub fn new(headers: HashMap<String, String>, body: impl Into<String>) -> Self {
        Request {
            headers,
            body: body.into(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Response {
    pub status: u16,
    pub body: String,
}

pub fn process_request(req: Request) -> Response {
    let normalised = normalize_headers(req.headers);

    match check_auth(&normalised) {
        Ok(()) => Response {
            status: 200,
            body: "OK".to_string(),
        },
        Err(reason) => Response {
            status: 401,
            body: format!("Unauthorized: {}", reason),
        },
    }
}
