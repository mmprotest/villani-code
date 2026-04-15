use std::collections::HashMap;

pub struct Config {
    flags: HashMap<String, bool>,
}

impl Config {
    pub fn new() -> Self {
        Config {
            flags: HashMap::new(),
        }
    }

    pub fn set_flag(&mut self, name: &str, value: bool) {
        self.flags.insert(name.to_string(), value);
    }

    pub fn is_enabled(&self, name: &str) -> bool {
        *self.flags.get(name).unwrap_or(&false)
    }
}
