import math

def retry_delay_seconds(header):
    if header is None:
        return 1
    return max(1, math.ceil(float(header) + 1))
