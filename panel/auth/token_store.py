from cachetools import TTLCache
_token_cache = TTLCache(maxsize=1000, ttl=60*10)  # 10 min

def save_token(session_id: str, token: str):
    _token_cache[session_id] = token

def get_token(session_id: str) -> str | None:
    return _token_cache.get(session_id)
