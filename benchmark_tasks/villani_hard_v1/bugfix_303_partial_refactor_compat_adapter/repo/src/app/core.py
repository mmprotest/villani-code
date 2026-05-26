def build_lines(title: str, items: list[str]) -> list[str]:
    return [title.upper(), *[f'- {item}' for item in items]]
