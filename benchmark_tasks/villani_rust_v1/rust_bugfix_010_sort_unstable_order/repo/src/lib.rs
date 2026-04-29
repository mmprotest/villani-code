#[derive(Debug, Clone, PartialEq)]
pub struct Record {
    pub name: String,
    pub priority: u32,
}

impl Record {
    pub fn new(name: &str, priority: u32) -> Self {
        Record {
            name: name.to_string(),
            priority,
        }
    }
}

pub fn sort_records(records: &mut Vec<Record>) {
    records.sort_by(|a, b| {
        a.priority
            .cmp(&b.priority)
            .then(b.name.cmp(&a.name))
    });
}
