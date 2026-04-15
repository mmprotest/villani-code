use user_serde::{from_json, to_json, User};

#[test]
fn roundtrip_preserves_user_name() {
    let original = User {
        user_name: "alice".to_string(),
        email_address: "alice@example.com".to_string(),
    };
    let json = to_json(&original);
    let restored: User = from_json(&json).expect("deserialization should succeed");
    assert_eq!(restored.user_name, original.user_name);
}

#[test]
fn roundtrip_preserves_email_address() {
    let original = User {
        user_name: "bob".to_string(),
        email_address: "bob@example.com".to_string(),
    };
    let json = to_json(&original);
    let restored: User = from_json(&json).expect("deserialization should succeed");
    assert_eq!(restored.email_address, original.email_address);
}

#[test]
fn serialized_json_uses_camel_case_keys() {
    let user = User {
        user_name: "carol".to_string(),
        email_address: "carol@example.com".to_string(),
    };
    let json = to_json(&user);
    assert!(json.contains("userName"), "JSON should contain 'userName' key");
    assert!(json.contains("emailAddress"), "JSON should contain 'emailAddress' key");
}
