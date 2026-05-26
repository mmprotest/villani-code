from .contracts import normalize_user_payload

def create_user(payload: dict) -> dict:
    n = normalize_user_payload(payload)
    return {'user': {'display_name': n['full_name'], 'email': n['email']}, 'status': 'created'}
