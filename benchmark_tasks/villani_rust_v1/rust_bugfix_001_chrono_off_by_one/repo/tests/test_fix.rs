use chrono::{TimeZone, Utc};
use date_resolver::resolve_today;

#[test]
fn test_resolve_today_returns_correct_date() {
    let now = Utc.with_ymd_and_hms(2024, 3, 15, 10, 0, 0).unwrap();
    let result = resolve_today(now);
    assert_eq!(
        result.to_string(),
        "2024-03-15",
        "resolve_today should return today's date, not tomorrow's"
    );
}

#[test]
fn test_resolve_today_near_midnight() {
    let now = Utc.with_ymd_and_hms(2024, 6, 1, 23, 30, 0).unwrap();
    let result = resolve_today(now);
    assert_eq!(
        result.to_string(),
        "2024-06-01",
        "resolve_today near midnight should still return the current date"
    );
}
