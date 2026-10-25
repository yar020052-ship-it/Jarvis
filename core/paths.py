import json
import os
import sys
from pathlib import Path


def get_app_dir() -> Path:
    """Директория приложения (не трогаем при обновлении)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.resolve()
    return Path(__file__).resolve().parent.parent


def get_user_data_dir() -> Path:
    """Пользовательские данные (конфиги, БД, память, логи, кэш).

    На Windows путь строим от Path.home(), а НЕ от %LOCALAPPDATA%:
    Store-версии Python подменяют LOCALAPPDATA на свой LocalCache, из-за чего
    настройки «то есть, то нет» в зависимости от того, каким python запустили.
    """
    app_name = "JARVIS"

    if sys.platform == "win32":
        try:
            return Path.home() / "AppData" / "Local" / app_name
        except Exception:
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            if base:
                return Path(base) / app_name
            return Path.home() / "AppData" / "Local" / app_name

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name

    # Linux/Unix
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / app_name
    return Path.home() / ".local" / "share" / app_name


def get_config_dir() -> Path:
    """Директория конфигурации (user-data)."""
    return get_user_data_dir() / "config"


def get_config_path(name: str) -> Path:
    """Return the active config path, preferring the user's settings folder.

    Older releases kept configuration beside the application. Keep that path
    as a read fallback for installs that have not migrated yet, but use the
    user-data copy as soon as it exists so UI edits affect the running app.
    """
    filename = str(name or "").strip()
    if (
        not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
        or any(separator in filename for separator in ("/", "\\", ":"))
    ):
        raise ValueError("Configuration name must be a plain filename.")
    user_path = get_config_dir() / filename
    if user_path.is_file():
        return user_path
    legacy_path = get_app_dir() / "config" / filename
    if legacy_path.is_file():
        return legacy_path
    return user_path


def load_config(name: str, *, include_legacy: bool = False) -> dict:
    """Load JSON settings, optionally filling fields absent from the user copy.

    The legacy merge is opt-in for non-secret defaults such as a preferred
    model. Credentials and ordinary runtime settings use the user copy alone,
    so clearing a saved key never resurrects an older one from the app folder.
    """
    selected_path = get_config_path(name)  # also validates that this is a filename
    filename = selected_path.name
    user_path = get_config_dir() / filename
    legacy_path = get_app_dir() / "config" / filename
    user_exists = user_path.is_file()
    path = user_path if user_exists else legacy_path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    if not include_legacy or not user_exists:
        return value
    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return value
    if not isinstance(legacy, dict):
        return value
    legacy.update(value)
    return legacy


def get_memory_dir() -> Path:
    """Директория памяти (user-data)."""
    return get_user_data_dir() / "memory"


def get_logs_dir() -> Path:
    """Директория логов (user-data)."""
    return get_user_data_dir() / "logs"


def get_cache_dir() -> Path:
    """Директория кеша (user-data)."""
    return get_user_data_dir() / "cache"


def get_models_dir() -> Path:
    """Директория моделей (user-data, тяжёлые файлы)."""
    return get_user_data_dir() / "models"


def ensure_user_dirs() -> None:
    """Создаём все пользовательские директории при необходимости."""
    for d in (
        get_user_data_dir(),
        get_config_dir(),
        get_memory_dir(),
        get_logs_dir(),
        get_cache_dir(),
        get_models_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)
    # Разовый перенос старых файлов из папки приложения в user-data,
    # чтобы после обновления не приходилось всё вводить заново.
    # Только копируем (не удаляем), только если в user-data файла ещё нет.
    try:
        migrate_legacy_user_files()
    except Exception:
        pass


_LEGACY_MIGRATION_VERSION = 1


def migrate_legacy_user_files() -> None:
    """Копирует legacy-файлы из APP_DIR в user-data, если их там нет.

    Покрывает главный кейс: config/api_keys.json (ключи, имена, устройства,
    настройки плагинов). Остальные config/*.json копируем тоже, если их нет.
    """
    try:
        app_dir = get_app_dir()
        cfg_dir = get_config_dir()
        cfg_dir.mkdir(parents=True, exist_ok=True)
        marker = cfg_dir / ".migration_version"
        try:
            current = int(marker.read_text(encoding="utf-8").strip()) if marker.exists() else 0
        except (OSError, ValueError):
            current = 0
        if current >= _LEGACY_MIGRATION_VERSION:
            return
        legacy_cfg = app_dir / "config"
        if legacy_cfg.is_dir():
            for name in (
                "api_keys.json",
                "mcp_servers.json",
                "workflows.json",
                "workspace.json",
                "device_sessions.json",
                "chat_history.json",
            ):
                src = legacy_cfg / name
                dst = cfg_dir / name
                try:
                    if src.exists() and src.is_file() and not dst.exists():
                        import shutil

                        shutil.copy2(src, dst)
                except Exception:
                    continue
        try:
            marker.write_text(str(_LEGACY_MIGRATION_VERSION), encoding="utf-8")
        except OSError:
            pass
    except Exception:
        pass


# Основные пути
APP_DIR = get_app_dir()
USER_DATA_DIR = get_user_data_dir()
CONFIG_DIR = get_config_dir()
MEMORY_DIR = get_memory_dir()
LOGS_DIR = get_logs_dir()
CACHE_DIR = get_cache_dir()
MODELS_DIR = get_models_dir()


__all__ = [
    "APP_DIR",
    "USER_DATA_DIR",
    "CONFIG_DIR",
    "MEMORY_DIR",
    "LOGS_DIR",
    "CACHE_DIR",
    "MODELS_DIR",
    "get_app_dir",
    "get_user_data_dir",
    "get_config_dir",
    "get_config_path",
    "load_config",
    "get_memory_dir",
    "get_logs_dir",
    "get_cache_dir",
    "get_models_dir",
    "ensure_user_dirs",
]
