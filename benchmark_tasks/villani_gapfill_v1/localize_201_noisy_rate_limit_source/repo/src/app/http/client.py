DEFAULT_DELAY = 1

def choose_delay(header):
    return DEFAULT_DELAY if header is None else int(float(header))
