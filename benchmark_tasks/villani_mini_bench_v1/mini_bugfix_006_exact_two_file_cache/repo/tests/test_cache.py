from app.cache import Cache


def test_dict_keys_round_trip_regardless_of_key_order():
    cache = Cache()
    cache.put('users', {'page': 1, 'filter': 'new'}, {'rows': 3})
    assert cache.get('users', {'filter': 'new', 'page': 1}) == {'rows': 3}


def test_list_keys_round_trip():
    cache = Cache()
    cache.put('feed', ['a', 'b'], 7)
    assert cache.get('feed', ['a', 'b']) == 7
