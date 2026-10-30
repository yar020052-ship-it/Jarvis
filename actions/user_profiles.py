"""User profiles for JARVIS — multi-user support.

Manages user profiles with separate settings, memory, and preferences.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILES_DIR = BASE_DIR / "profiles"


def _ensure_profiles_dir():
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def list_profiles(parameters=None, response=None, player=None, session_memory=None) -> str:
    """List all user profiles."""
    _ensure_profiles_dir()
    profiles = []
    for d in PROFILES_DIR.iterdir():
        if d.is_dir():
            config_file = d / "profile.json"
            if config_file.exists():
                try:
                    data = json.loads(config_file.read_text(encoding="utf-8"))
                    profiles.append(f"  • {data.get('name', d.name)} (ID: {d.name})")
                except Exception:
                    profiles.append(f"  • {d.name}")
            else:
                profiles.append(f"  • {d.name}")
    if not profiles:
        return "Профилей нет. Создайте первый профиль."
    return f"Профили ({len(profiles)}):\n" + "\n".join(profiles)


def create_profile(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Create a new user profile."""
    params = parameters or {}
    name = params.get("name", "").strip()
    if not name:
        return "Укажите имя профиля (name)."

    _ensure_profiles_dir()
    profile_id = name.lower().replace(" ", "_")
    profile_dir = PROFILES_DIR / profile_id
    if profile_dir.exists():
        return f"Профиль '{name}' уже существует."

    profile_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "name": name,
        "id": profile_id,
        "created": __import__("datetime").datetime.now().isoformat(),
        "assistant_name": "JARVIS",
        "ui_color": "#00e5ff",
    }
    (profile_dir / "profile.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return f"Профиль '{name}' создан (ID: {profile_id})."


def switch_profile(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Switch to a different user profile."""
    params = parameters or {}
    profile_id = params.get("profile_id", "").strip()
    if not profile_id:
        return "Укажите ID профиля (profile_id)."

    profile_dir = PROFILES_DIR / profile_id
    if not profile_dir.exists():
        return f"Профиль '{profile_id}' не найден."

    # Save current profile ID
    current_file = BASE_DIR / "config" / "current_profile.txt"
    current_file.write_text(profile_id, encoding="utf-8")
    return f"Переключено на профиль '{profile_id}'. Перезапустите JARVIS для применения."


def get_current_profile(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Get the current active profile."""
    current_file = BASE_DIR / "config" / "current_profile.txt"
    if current_file.exists():
        pid = current_file.read_text(encoding="utf-8").strip()
        profile_file = PROFILES_DIR / pid / "profile.json"
        if profile_file.exists():
            data = json.loads(profile_file.read_text(encoding="utf-8"))
            return f"Текущий профиль: {data.get('name', pid)} (ID: {pid})"
    return "Профиль по умолчанию (не выбран)"


TOOL = {
    "name": "user_profiles",
    "description": "Manage multiple user profiles for JARVIS.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list, create, switch, or current",
            },
            "name": {
                "type": "STRING",
                "description": "Profile name (for create)",
            },
            "profile_id": {
                "type": "STRING",
                "description": "Profile ID (for switch)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        list_profiles(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "list").lower() == "list"
        else (
            create_profile(parameters, response, player, session_memory)
            if (parameters or {}).get("action", "").lower() == "create"
            else (
                switch_profile(parameters, response, player, session_memory)
                if (parameters or {}).get("action", "").lower() == "switch"
                else get_current_profile(parameters, response, player, session_memory)
            )
        )
    ),
}