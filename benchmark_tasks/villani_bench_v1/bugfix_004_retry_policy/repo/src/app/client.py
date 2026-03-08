def send_with_retry(statuses: list[int], max_retries: int = 2) -> int:
    attempts = 0
    for status in statuses:
        if status < 500 and attempts < max_retries:
            attempts += 1
            continue
        if status in {500, 502, 503, 504, 429} and attempts < max_retries:
            attempts += 1
            continue
        return attempts + 1
    return attempts + 1
