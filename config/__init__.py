# config/__init__.py
import json, os, platform
from pathlib import Path

try:
    from core.paths import CONFIG_DIR, ensure_user_dirs

    ensure_user_dirs()
    _CONFIG_PATH = CONFIG_DIR / "api_keys.json"
    # Миграция старого конфига из app/config в user-data, если он существует
    _old = Path(__file__).parent / "api_keys.json"
    if not _CONFIG_PATH.exists() and _old.exists():
        try:
            import shutil

            _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_old, _CONFIG_PATH)
        except Exception:
            _CONFIG_PATH = _old
except Exception:
    _CONFIG_PATH = Path(__file__).parent / "api_keys.json"


def _platform_os() -> str:
    """Auto-detect OS when config file is absent."""
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )


def get_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", _platform_os()).lower()


def is_windows() -> bool:
    return get_os() == "windows"


def is_mac() -> bool:
    return get_os() == "mac"


def is_linux() -> bool:
    return get_os() == "linux"
