import time

_cache = {}
MAX_CACHE_SIZE = 300


def _evict():
    if len(_cache) > MAX_CACHE_SIZE:
        for key in sorted(_cache, key=lambda item: _cache[item]["expires"])[: MAX_CACHE_SIZE // 5]:
            _cache.pop(key, None)


def cache_get(key):
    item = _cache.get(key)
    if item and time.time() < item["expires"]:
        return item["value"]
    _cache.pop(key, None)
    return None


def cache_set(key, value, ttl=60):
    _evict()
    _cache[key] = {"value": value, "expires": time.time() + ttl}


def cache_del(key):
    _cache.pop(key, None)


def cache_clear_prefix(prefix):
    for key in [item for item in _cache if item.startswith(prefix)]:
        _cache.pop(key, None)
