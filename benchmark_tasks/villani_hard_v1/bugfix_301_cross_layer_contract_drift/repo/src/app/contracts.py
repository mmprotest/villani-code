from __future__ import annotations

def normalize_user_payload(payload: dict) -> dict:
    full_name = payload.get('full_name')
    email = payload.get('email', '').strip().lower()
    if not full_name:
        raise ValueError('full_name is required')
    if not email:
        raise ValueError('email is required')
    return {'full_name': full_name.strip(), 'email': email}
