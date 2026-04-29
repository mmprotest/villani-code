use std::collections::HashMap;
use crate::models::User;

pub struct UserCache {
    store: HashMap<String, User>,
}

impl UserCache {
    pub fn new() -> Self {
        UserCache {
            store: HashMap::new(),
        }
    }

    pub fn insert(&mut self, user: User) {
        self.store.insert(user.username.clone(), user);
    }

    pub fn get_by_username(&self, username: &str) -> Option<&User> {
        self.store.get(username)
    }

    pub fn get_by_email(&self, email: &str) -> Option<&User> {
        self.store.get(email)
    }
}
