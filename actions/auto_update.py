"""Auto-update for JARVIS — check for and apply updates.

Checks for new versions on GitHub and applies updates via git pull.
"""

import json
import subprocess
import urllib.request
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE = BASE_DIR / "VERSION"
GITHUB_REPO = "https://api.github.com/repos/akrom/JARVIS/releases/latest"


def _current_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "unknown"


def check_update(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Check for available updates."""
    try:
        with urllib.request.urlopen(GITHUB_REPO, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = data.get("tag_name", "unknown")
        current = _current_version()
        if latest == current or latest == "unknown":
            return f"JARVIS актуален (версия {current})."
        return (
            f"Доступно обновление: {latest} (текущая: {current}).\n"
            f"Примечания: {data.get('body', '')[:500]}"
        )
    except Exception as e:
        return f"Не удалось проверить обновления: {e}"


def apply_update(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Apply available update via git pull."""
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(BASE_DIR),
        )
        if result.returncode == 0:
            return f"Обновление применено:\n{result.stdout[:500]}"
        return f"Ошибка обновления: {result.stderr[:500]}"
    except Exception as e:
        return f"Не удалось применить обновление: {e}"


def get_version(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Get current JARVIS version."""
    return f"Текущая версия JARVIS: {_current_version()}"


TOOL = {
    "name": "auto_update",
    "description": "Check for and apply JARVIS updates.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "check (check for updates), apply (apply update), or version (show current version)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        check_update(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "check").lower() == "check"
        else (
            apply_update(parameters, response, player, session_memory)
            if (parameters or {}).get("action", "").lower() == "apply"
            else get_version(parameters, response, player, session_memory)
        )
    ),
}