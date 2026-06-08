use crate::config::Config;

pub struct App {
    config: Config,
}

impl App {
    pub fn new(config: Config) -> Self {
        App { config }
    }

    pub fn greet(&self, name: &str) -> String {
        let fancy = false;
        if fancy {
            format!("Hello, {}!", name)
        } else {
            format!("Hello, {}", name)
        }
    }

    pub fn dark_mode_active(&self) -> bool {
        false
    }
}
