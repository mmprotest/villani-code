from app.core.v2 import NormalizedUser
from app.legacy.v1 import LegacyUser

def to_v2(user):
    if isinstance(user, LegacyUser): return NormalizedUser(user.uid, user.name)
    return user

def to_v1(user):
    if isinstance(user, NormalizedUser): return LegacyUser(user.user_id, user.display_name)
    return user
