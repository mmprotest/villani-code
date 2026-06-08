use record_sort::{sort_records, Record};

#[test]
fn records_sorted_by_priority_ascending() {
    let mut records = vec![
        Record::new("high", 3),
        Record::new("low", 1),
        Record::new("mid", 2),
    ];
    sort_records(&mut records);
    assert_eq!(records[0].name, "low");
    assert_eq!(records[1].name, "mid");
    assert_eq!(records[2].name, "high");
}

#[test]
fn equal_priority_preserves_insertion_order() {
    let mut records = vec![
        Record::new("first", 1),
        Record::new("alpha", 2),
        Record::new("beta", 2),
        Record::new("last", 3),
    ];
    sort_records(&mut records);
    assert_eq!(records[0].name, "first");
    assert_eq!(records[1].name, "alpha", "alpha should retain insertion order before beta");
    assert_eq!(records[2].name, "beta", "beta should retain insertion order after alpha");
    assert_eq!(records[3].name, "last");
}

#[test]
fn all_equal_priority_preserves_insertion_order() {
    let mut records = vec![
        Record::new("a", 5),
        Record::new("b", 5),
        Record::new("c", 5),
        Record::new("d", 5),
    ];
    sort_records(&mut records);
    let names: Vec<&str> = records.iter().map(|r| r.name.as_str()).collect();
    assert_eq!(names, vec!["a", "b", "c", "d"]);
}
