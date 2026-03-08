def should_retry(status: int) -> bool:
    return status in {429,500,502,503,504}
