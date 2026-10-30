"""Cloud backup and sync for JARVIS — export/import config and memory.

Creates portable backup archives of JARVIS configuration, memory, and settings.
"""

import json
import zipfile
import shutil
from pathlib import Path
from datetime import datetime
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
MEMORY_DIR = BASE_DIR / "memory"
BACKUP_DIR = BASE_DIR / "backups"


def create_backup(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Create a backup archive of JARVIS data."""
    params = parameters or {}
    include_memory = params.get("include_memory", True)

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"jarvis_backup_{timestamp}.zip"

        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Config files
            if CONFIG_DIR.exists():
                for f in CONFIG_DIR.iterdir():
                    if f.is_file():
                        zf.write(f, f"config/{f.name}")
            # Memory files
            if include_memory and MEMORY_DIR.exists():
                for f in MEMORY_DIR.rglob("*"):
                    if f.is_file():
                        zf.write(f, f"memory/{f.relative_to(MEMORY_DIR)}")
            # User data
            data_dir = BASE_DIR / "data"
            if data_dir.exists():
                for f in data_dir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f"data/{f.relative_to(data_dir)}")

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        return f"Бэкап создан: {backup_path.name} ({size_mb:.2f} МБ)\nПуть: {backup_path}"
    except Exception as e:
        return f"Ошибка создания бэкапа: {e}"


def restore_backup(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Restore JARVIS from a backup archive."""
    params = parameters or {}
    backup_path = params.get("backup_path", "").strip()

    if not backup_path:
        # List available backups
        if not BACKUP_DIR.exists():
            return "Бэкапов не найдено."
        backups = sorted(BACKUP_DIR.glob("jarvis_backup_*.zip"), reverse=True)
        if not backups:
            return "Бэкапов не найдено."
        return "Доступные бэкапы:\n" + "\n".join(f"  • {b.name}" for b in backups[:10])

    p = Path(backup_path)
    if not p.exists():
        p = BACKUP_DIR / backup_path
    if not p.exists():
        return f"Файл бэкапа не найден: {backup_path}"

    try:
        with zipfile.ZipFile(p, "r") as zf:
            zf.extractall(BASE_DIR)
        return f"Бэкап восстановлен из {p.name}. Перезапустите JARVIS для применения."
    except Exception as e:
        return f"Ошибка восстановления: {e}"


def list_backups(parameters=None, response=None, player=None, session_memory=None) -> str:
    """List available backups."""
    if not BACKUP_DIR.exists():
        return "Бэкапов не найдено."
    backups = sorted(BACKUP_DIR.glob("jarvis_backup_*.zip"), reverse=True)
    if not backups:
        return "Бэкапов не найдено."
    lines = []
    for b in backups[:20]:
        size_mb = b.stat().st_size / (1024 * 1024)
        lines.append(f"  • {b.name} ({size_mb:.2f} МБ)")
    return f"Бэкапы ({len(backups)}):\n" + "\n".join(lines)


TOOL = {
    "name": "backup_sync",
    "description": "Create, restore, and list backups of JARVIS configuration and memory.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "create, restore, or list",
            },
            "backup_path": {
                "type": "STRING",
                "description": "Path to backup file (for restore)",
            },
            "include_memory": {
                "type": "BOOLEAN",
                "description": "Include memory files in backup (default: true)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        create_backup(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "create").lower() == "create"
        else (
            restore_backup(parameters, response, player, session_memory)
            if (parameters or {}).get("action", "").lower() == "restore"
            else list_backups(parameters, response, player, session_memory)
        )
    ),
}