use user_cache::cache::UserCache;
use user_cache::models::User;

fn make_user() -> User {
    User {
        id: 1,
        username: "alice".to_string(),
        email: "alice@example.com".to_string(),
    }
}

#[test]
fn test_serialize_deserialize_roundtrip() {
    let user = make_user();
    let json = serde_json::to_string(&user).unwrap();
    let restored: User = serde_json::from_str(&json).unwrap();
    assert_eq!(restored, user);
}

#[test]
fn test_deserialize_from_canonical_json() {
    let json = r#"{"id":1,"username":"alice","email":"alice@example.com"}"#;
    let user: User = serde_json::from_str(json).unwrap();
    assert_eq!(
        user.email, "alice@example.com",
        "email field should be populated from JSON key 'email', got: {:?}",
        user.email
    );
}

#[test]
fn test_cache_hit_by_username() {
    let mut cache = UserCache::new();
    cache.insert(make_user());
    let found = cache.get_by_username("alice");
    assert!(found.is_some(), "Should find user by username");
    assert_eq!(found.unwrap().email, "alice@example.com");
}

#[test]
fn test_cache_hit_by_email() {
    let mut cache = UserCache::new();
    cache.insert(make_user());
    let found = cache.get_by_email("alice@example.com");
    assert!(
        found.is_some(),
        "Should find user by email but got None — cache lookup is broken"
    );
    assert_eq!(found.unwrap().username, "alice");
}

#[test]
fn test_cache_miss_for_unknown_email() {
    let mut cache = UserCache::new();
    cache.insert(make_user());
    assert!(cache.get_by_email("nobody@example.com").is_none());
}
