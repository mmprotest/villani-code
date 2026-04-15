use config_loader::resolve_config;
use std::path::Path;

#[test]
fn base_dir_is_respected_when_filename_has_leading_slash() {
    let base = Path::new("/home/app");
    let result = resolve_config(base, "/config/app.toml");
    assert_eq!(result, Path::new("/home/app/config/app.toml"));
}

#[test]
fn relative_filename_is_joined_correctly() {
    let base = Path::new("/home/app");
    let result = resolve_config(base, "config/app.toml");
    assert_eq!(result, Path::new("/home/app/config/app.toml"));
}

#[test]
fn nested_base_and_relative_filename() {
    let base = Path::new("/srv/myapp/data");
    let result = resolve_config(base, "/settings.json");
    assert_eq!(result, Path::new("/srv/myapp/data/settings.json"));
}
