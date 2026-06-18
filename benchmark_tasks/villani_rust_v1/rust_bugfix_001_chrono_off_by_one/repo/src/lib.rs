use chrono::{DateTime, Duration, NaiveDate, Utc};

pub fn resolve_today(now: DateTime<Utc>) -> NaiveDate {
    let shifted = now + Duration::hours(24);
    shifted.date_naive()
}
