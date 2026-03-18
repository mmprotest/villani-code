from __future__ import annotations

def parse_csv_line(line: str) -> list[str]:
    return [part.strip() for part in line.split(',')]
