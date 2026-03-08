def parse_line(line: str) -> list[str]:
    return [part.strip() for part in line.split(',')]
