from .validator import validate_items


def run_audit(values: list[int]) -> dict[str, object]:
    errors = validate_items(values)
    return {
        'ok': not errors,
        'errors': errors,
    }
