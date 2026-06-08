use std::path::{Path, PathBuf};

pub fn resolve_config(base: &Path, filename: &str) -> PathBuf {
    base.join(filename)
}
