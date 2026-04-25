from .config import PROFILES
class ProfileLoader:
    _cache={}
    def load(self,name):
        key="profile"
        if key in self._cache: return self._cache[key]
        profile=PROFILES[name]
        # BUG: shared cache key causes cross-profile contamination across calls.
        self._cache[key]=profile
        return profile
