"""In-memory theme cache (supports hot-reload)."""

_cache: dict[str, dict] = {}


def get(theme_id: str) -> dict | None:
    return _cache.get(theme_id)


def set(theme_id: str, data: dict):
    _cache[theme_id] = data


def clear():
    _cache.clear()
