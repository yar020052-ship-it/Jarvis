"""Accessibility settings for JARVIS — high contrast, font scaling, screen reader hints.

Provides accessibility configuration options stored in config.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "accessibility.json"


def _load_settings() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "high_contrast": False,
        "font_scale": 1.0,
        "reduce_motion": False,
        "screen_reader_hints": True,
        "large_cursor": False,
    }


def _save_settings(settings: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def get_settings(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Get current accessibility settings."""
    s = _load_settings()
    lines = [
        f"Высокий контраст: {'вкл' if s.get('high_contrast') else 'выкл'}",
        f"Масштаб шрифта: {s.get('font_scale', 1.0)}x",
        f"Уменьшение анимации: {'вкл' if s.get('reduce_motion') else 'выкл'}",
        f"Подсказки для скринридера: {'вкл' if s.get('screen_reader_hints') else 'выкл'}",
        f"Большой курсор: {'вкл' if s.get('large_cursor') else 'выкл'}",
    ]
    return "Настройки доступности:\n" + "\n".join(lines)


def set_settings(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Update accessibility settings."""
    params = parameters or {}
    s = _load_settings()

    if "high_contrast" in params:
        s["high_contrast"] = bool(params["high_contrast"])
    if "font_scale" in params:
        scale = float(params["font_scale"])
        s["font_scale"] = max(0.5, min(3.0, scale))
    if "reduce_motion" in params:
        s["reduce_motion"] = bool(params["reduce_motion"])
    if "screen_reader_hints" in params:
        s["screen_reader_hints"] = bool(params["screen_reader_hints"])
    if "large_cursor" in params:
        s["large_cursor"] = bool(params["large_cursor"])

    _save_settings(s)
    return "Настройки доступности обновлены. Перезапустите JARVIS для применения."


TOOL = {
    "name": "accessibility",
    "description": "Configure accessibility settings: high contrast, font scaling, reduce motion, screen reader hints.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "get (show settings) or set (update settings)",
            },
            "high_contrast": {
                "type": "BOOLEAN",
                "description": "Enable high contrast mode",
            },
            "font_scale": {
                "type": "NUMBER",
                "description": "Font scale factor (0.5 to 3.0)",
            },
            "reduce_motion": {
                "type": "BOOLEAN",
                "description": "Reduce animations",
            },
            "screen_reader_hints": {
                "type": "BOOLEAN",
                "description": "Enable screen reader hints",
            },
            "large_cursor": {
                "type": "BOOLEAN",
                "description": "Enable large cursor",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        get_settings(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "get").lower() == "get"
        else set_settings(parameters, response, player, session_memory)
    ),
}