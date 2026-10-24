"""Minimal internationalization helpers for JARVIS.

Strings are stored as JSON dictionaries under ``core/locales/<lang>.json``.
The active language is selected with :func:`set_language` (defaults to ``ru``)
and can be overridden through the ``JARVIS_LANG`` environment variable. Missing
keys fall back to the key itself (or to an explicit default), so adding a new
language never requires changing Python code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_LOCALES_DIR = Path(__file__).resolve().parent / "locales"
_DEFAULT_LANGUAGE = "ru"
_current_language = os.environ.get("JARVIS_LANG", _DEFAULT_LANGUAGE).strip().lower() or _DEFAULT_LANGUAGE
_cache: dict[str, dict[str, str]] = {}


def available_languages() -> list[str]:
    """Return the language codes that have a locale file."""
    if not _LOCALES_DIR.is_dir():
        return [_DEFAULT_LANGUAGE]
    return sorted(path.stem for path in _LOCALES_DIR.glob("*.json"))


def set_language(language: str) -> str:
    """Set the active language and return the code actually used."""
    global _current_language
    code = (language or "").strip().lower()
    if code not in available_languages():
        code = _DEFAULT_LANGUAGE
    _current_language = code
    return _current_language


def get_language() -> str:
    return _current_language


def _load(language: str) -> dict[str, str]:
    if language in _cache:
        return _cache[language]
    path = _LOCALES_DIR / f"{language}.json"
    data: dict[str, str] = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = {str(k): str(v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        data = {}
    _cache[language] = data
    return data


def t(key: str, default: str | None = None, **kwargs: Any) -> str:
    """Translate ``key`` using the active language.

    Falls back to ``default`` (or ``key``) when the key is missing, and applies
    ``str.format`` with ``kwargs`` when provided.
    """
    text = _load(_current_language).get(key)
    if text is None:
        if _current_language != _DEFAULT_LANGUAGE:
            text = _load(_DEFAULT_LANGUAGE).get(key)
        if text is None:
            text = default if default is not None else key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            return text
    return text


# Convenience alias, matching the common ``_`` shorthand.
_ = t