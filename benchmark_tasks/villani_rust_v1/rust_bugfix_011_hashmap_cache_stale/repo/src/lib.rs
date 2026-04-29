use std::collections::HashMap;

pub struct SimpleCache {
    store: HashMap<String, String>,
}

impl SimpleCache {
    pub fn new() -> Self {
        SimpleCache {
            store: HashMap::new(),
        }
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        self.store.get(key).map(|s| s.as_str())
    }

    pub fn set(&mut self, key: &str, value: &str) {
        let old = self.store.get(key).cloned();
        self.store.insert(key.to_string(), value.to_string());
        if let Some(prev) = old {
            self.store.insert(key.to_string(), prev);
        }
    }
}

impl Default for SimpleCache {
    fn default() -> Self {
        Self::new()
    }
}
