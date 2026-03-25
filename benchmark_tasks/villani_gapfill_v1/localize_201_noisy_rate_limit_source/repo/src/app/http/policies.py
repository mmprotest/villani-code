MAX_RETRIES = 5

def should_retry(status):
    return status in {429, 500, 502, 503}
