use hashmap_cache::SimpleCache;

#[test]
fn test_set_then_get_returns_new_value() {
    let mut cache = SimpleCache::new();
    cache.set("key", "v1");
    assert_eq!(
        cache.get("key"),
        Some("v1"),
        "first set should store 'v1'"
    );
    cache.set("key", "v2");
    assert_eq!(
        cache.get("key"),
        Some("v2"),
        "after update, get should return 'v2' not the stale 'v1'"
    );
}

#[test]
fn test_insert_new_key() {
    let mut cache = SimpleCache::new();
    assert_eq!(cache.get("missing"), None, "absent key should return None");
    cache.set("new_key", "hello");
    assert_eq!(
        cache.get("new_key"),
        Some("hello"),
        "newly inserted key should be retrievable"
    );
}

#[test]
fn test_multiple_keys_independent() {
    let mut cache = SimpleCache::new();
    cache.set("a", "alpha");
    cache.set("b", "beta");
    cache.set("a", "ALPHA");
    assert_eq!(
        cache.get("a"),
        Some("ALPHA"),
        "key 'a' should reflect updated value"
    );
    assert_eq!(
        cache.get("b"),
        Some("beta"),
        "key 'b' should be unaffected by update to 'a'"
    );
}
