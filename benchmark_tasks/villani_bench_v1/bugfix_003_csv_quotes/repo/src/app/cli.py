from .csv_parser import parse_line

def parse(text: str) -> list[str]:
    return parse_line(text)
