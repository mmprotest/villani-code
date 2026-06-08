use serde::{Deserialize, Serialize};

#[derive(Debug, PartialEq, Serialize, Deserialize)]
pub struct User {
    #[serde(rename(serialize = "userName"))]
    pub user_name: String,

    #[serde(rename(serialize = "emailAddress"))]
    pub email_address: String,
}

pub fn to_json(user: &User) -> String {
    serde_json::to_string(user).expect("serialization failed")
}

pub fn from_json(s: &str) -> Result<User, serde_json::Error> {
    serde_json::from_str(s)
}
