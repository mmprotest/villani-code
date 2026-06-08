use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct User {
    pub id: u32,
    pub username: String,
    #[serde(rename = "email_address", default)]
    pub email: String,
}
