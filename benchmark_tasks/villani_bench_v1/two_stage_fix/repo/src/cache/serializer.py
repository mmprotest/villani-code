import json


def serialize(value: object) -> str:
    return json.dumps(value, sort_keys=True)
