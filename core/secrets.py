"""Optional OS keyring integration for secrets.

Falls back to the existing JSON config files when the ``keyring`` package or a
usable backend is unavailable, so JARVIS keeps working on minimal installs.
"""

from __future__ import annotations

from typing import Optional


def _backend():
    try:
        import keyring  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        return None
    try:
        keyring.get_password("jarvis", "__probe__")
    except Exception:  # pragma: no cover - no usable backend
        return None
    return keyring


def get_secret(name: str) -> Optional[str]:
    keyring = _backend()
    if keyring is None:
        return None
    try:
        value = keyring.get_password("jarvis", name)
    except Exception:  # pragma: no cover
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def set_secret(name: str, value: str) -> bool:
    keyring = _backend()
    if keyring is None:
        return False
    try:
        if value:
            keyring.set_password("jarvis", name, value)
        else:
            try:
                keyring.delete_password("jarvis", name)
            except Exception:
                pass
        return True
    except Exception:  # pragma: no cover
        return False