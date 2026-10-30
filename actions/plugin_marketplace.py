"""Plugin marketplace for JARVIS — browse and install plugins from a registry.

Provides a simple plugin discovery and installation mechanism.
"""

import json
import urllib.request
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_DIR = Path(__file__).resolve().parent.parent
PLUGINS_DIR = BASE_DIR / "plugins"
REGISTRY_URL = "https://raw.githubusercontent.com/jarvis-plugins/registry/main/plugins.json"


def browse_marketplace(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Browse available plugins in the marketplace."""
    params = parameters or {}
    query = params.get("query", "").strip().lower()

    try:
        with urllib.request.urlopen(REGISTRY_URL, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        plugins = data.get("plugins", [])
        if query:
            plugins = [p for p in plugins if query in p.get("name", "").lower() or query in p.get("description", "").lower()]
        if not plugins:
            return "Плагины не найдены."
        lines = []
        for p in plugins[:20]:
            lines.append(f"  • {p.get('name')} — {p.get('description', '')[:80]}")
            lines.append(f"    Версия: {p.get('version', 'N/A')} | Автор: {p.get('author', 'N/A')}")
        return f"Плагины в маркетплейсе ({len(plugins)}):\n" + "\n".join(lines)
    except Exception as e:
        # Fallback: show local plugins
        if PLUGINS_DIR.exists():
            local = [f.stem for f in PLUGINS_DIR.glob("*.py") if not f.name.startswith("_")]
            return f"Маркетплейс недоступен ({e}). Локальные плагины: {', '.join(local)}"
        return f"Ошибка: {e}"


def install_plugin(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Install a plugin from the marketplace."""
    params = parameters or {}
    name = params.get("name", "").strip()
    if not name:
        return "Укажите имя плагина (name)."

    try:
        url = f"https://raw.githubusercontent.com/jarvis-plugins/registry/main/plugins/{name}.py"
        with urllib.request.urlopen(url, timeout=15) as resp:
            code = resp.read().decode("utf-8")
        target = PLUGINS_DIR / f"{name}.py"
        target.write_text(code, encoding="utf-8")
        return f"Плагин '{name}' установлен. Перезапустите JARVIS для активации."
    except Exception as e:
        return f"Ошибка установки плагина '{name}': {e}"


def uninstall_plugin(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Uninstall a plugin."""
    params = parameters or {}
    name = params.get("name", "").strip()
    if not name:
        return "Укажите имя плагина (name)."

    target = PLUGINS_DIR / f"{name}.py"
    if not target.exists():
        return f"Плагин '{name}' не найден."
    try:
        target.unlink()
        return f"Плагин '{name}' удалён. Перезапустите JARVIS."
    except Exception as e:
        return f"Ошибка удаления: {e}"


TOOL = {
    "name": "plugin_marketplace",
    "description": "Browse, install, and uninstall plugins from the JARVIS plugin marketplace.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "browse, install, or uninstall",
            },
            "name": {
                "type": "STRING",
                "description": "Plugin name (for install/uninstall)",
            },
            "query": {
                "type": "STRING",
                "description": "Search query (for browse)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        browse_marketplace(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "browse").lower() == "browse"
        else (
            install_plugin(parameters, response, player, session_memory)
            if (parameters or {}).get("action", "").lower() == "install"
            else uninstall_plugin(parameters, response, player, session_memory)
        )
    ),
}