"""In-memory locale cache (supports hot-reload)."""

_cache: dict[str, dict] = {}


def get(locale_id: str) -> dict | None:
    return _cache.get(locale_id)


def set(locale_id: str, data: dict):
    _cache[locale_id] = data


def clear():
    _cache.clear()
