from .config import PROFILES, DEFAULT_PROFILE

def load_profile(name=None):
    if not name: return dict(DEFAULT_PROFILE)
    profile = dict(PROFILES[name])
    profile["normalize_metadata_key"] = DEFAULT_PROFILE["normalize_metadata_key"]
    return profile
