def parse_line(text: str) -> list[str]:
    return [chunk.strip() for chunk in text.replace("
",",").split(",")]
