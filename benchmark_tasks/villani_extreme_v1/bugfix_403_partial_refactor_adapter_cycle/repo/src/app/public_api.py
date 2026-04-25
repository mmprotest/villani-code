from app.adapters import to_v2

def greet_user(user): return f"hello {to_v2(user).display_name}"

def serialize_legacy_payload(user):
    modern = to_v2(user)
    return {"uid": modern.user_id, "display_name": modern.display_name}
