from .service import create_user

def post_user(payload: dict) -> dict:
    r = create_user(payload)
    return {'ok': True, 'user': r['user'], 'status': r['status']}
