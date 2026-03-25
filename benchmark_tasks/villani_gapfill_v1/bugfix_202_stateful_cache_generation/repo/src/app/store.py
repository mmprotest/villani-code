class Store:
    def __init__(self):
        self._db = {}
        self._cache = {}
        self._generation = 0

    def put(self, key, value):
        self._db[key] = value
        self._generation += 1

    def get(self, key):
        cached = self._cache.get(key)
        if cached is not None:
            value, generation = cached
            if generation <= self._generation:
                return value
        value = self._db.get(key)
        self._cache[key] = (value, self._generation)
        return value
