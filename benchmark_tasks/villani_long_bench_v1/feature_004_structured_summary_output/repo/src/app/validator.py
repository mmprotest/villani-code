def validate_items(values: list[int]) -> list[str]:
    errors = []
    for value in values:
        if value < 0:
            errors.append(f'{value}:must_be_non_negative')
    return errors
