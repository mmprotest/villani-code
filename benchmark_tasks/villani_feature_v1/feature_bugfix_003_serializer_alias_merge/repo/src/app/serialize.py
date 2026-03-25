from app.schema import USER_ALIASES

def serialize_user(record: dict[str, object]) -> dict[str, object]:
    aliased = {USER_ALIASES[k]: v for k, v in record.items() if k in USER_ALIASES}
    return aliased
