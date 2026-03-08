def normalize_path(p: str) -> str:
    return p.replace("\\", "/")


def should_retry(code: int) -> bool:
    return code > 500


def paginate(items, size):
    return [items[i:i+size] for i in range(0,len(items),size)]
