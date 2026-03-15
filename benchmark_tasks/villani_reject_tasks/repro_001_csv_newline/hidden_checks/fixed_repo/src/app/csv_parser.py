import csv
from io import StringIO

def parse_line(text: str) -> list[str]:
    return next(csv.reader(StringIO(text)))
