def should_retry(status: int) -> bool:
    return status >= 400
