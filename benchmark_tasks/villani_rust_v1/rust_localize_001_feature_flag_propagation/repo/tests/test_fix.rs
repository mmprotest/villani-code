use feature_flags::app::App;
use feature_flags::config::Config;

fn make_app_with_flags(flags: &[(&str, bool)]) -> App {
    let mut config = Config::new();
    for (name, value) in flags {
        config.set_flag(name, *value);
    }
    App::new(config)
}

#[test]
fn test_fancy_greeting_when_flag_disabled() {
    let app = make_app_with_flags(&[("fancy_greeting", false)]);
    assert_eq!(app.greet("Alice"), "Hello, Alice");
}

#[test]
fn test_fancy_greeting_when_flag_enabled() {
    let app = make_app_with_flags(&[("fancy_greeting", true)]);
    let result = app.greet("Alice");
    assert_eq!(
        result, "Hello, Alice!",
        "fancy_greeting flag is enabled but greeting has no exclamation: {:?}",
        result
    );
}

#[test]
fn test_dark_mode_off_by_default() {
    let app = make_app_with_flags(&[]);
    assert!(!app.dark_mode_active());
}

#[test]
fn test_dark_mode_on_when_flag_enabled() {
    let app = make_app_with_flags(&[("dark_mode", true)]);
    assert!(
        app.dark_mode_active(),
        "dark_mode flag is enabled in config but dark_mode_active() returned false"
    );
}
