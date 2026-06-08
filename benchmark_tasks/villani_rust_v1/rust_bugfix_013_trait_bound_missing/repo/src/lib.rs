fn print_items<T>(items: &[T]) -> Vec<String> {
    items.iter().map(|x| format!("{}", x)).collect()
}

pub fn collect_display<T>(items: &[T]) -> Vec<String> {
    print_items(items)
}
