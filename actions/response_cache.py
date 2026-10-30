"""Response cache for JARVIS — caches Gemini responses for offline use.

Stores recent query-response pairs in a local JSON file for offline access.
"""

import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CACHE_FILE = Path(__file__).resolve().parent.parent / "config" / "response_cache.json"
MAX_CACHE_ENTRIES = 200
CACHE_TTL_HOURS = 24


def _load_cache() -> list:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_cache(cache: list):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def cache_response(query: str, response: str):
    """Cache a query-response pair."""
    cache = _load_cache()
    key = hashlib.md5(query.encode()).hexdigest()
    # Remove old entry with same key
    cache = [e for e in cache if e.get("key") != key]
    cache.append({
        "key": key,
        "query": query[:500],
        "response": response[:2000],
        "timestamp": datetime.now().isoformat(),
    })
    # Trim to max size
    if len(cache) > MAX_CACHE_ENTRIES:
        cache = cache[-MAX_CACHE_ENTRIES:]
    _save_cache(cache)


def get_cached_response(query: str) -> str | None:
    """Get a cached response for a query."""
    cache = _load_cache()
    key = hashlib.md5(query.encode()).hexdigest()
    now = datetime.now()
    for entry in reversed(cache):
        if entry.get("key") == key:
            ts = datetime.fromisoformat(entry.get("timestamp", "2000-01-01"))
            if now - ts < timedelta(hours=CACHE_TTL_HOURS):
                return entry.get("response")
    return None


def clear_cache(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Clear the response cache."""
    _save_cache([])
    return "Кэш ответов очищен."


def cache_stats(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Show cache statistics."""
    cache = _load_cache()
    if not cache:
        return "Кэш пуст."
    oldest = cache[0].get("timestamp", "N/A")
    newest = cache[-1].get("timestamp", "N/A")
    return f"Записей в кэше: {len(cache)}\nСамая старая: {oldest}\nСамая новая: {newest}"


TOOL = {
    "name": "response_cache",
    "description": "Manage the offline response cache for JARVIS.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "clear (clear cache) or stats (show statistics)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        clear_cache(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "stats").lower() == "clear"
        else cache_stats(parameters, response, player, session_memory)
    ),
}