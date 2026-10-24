"""Window snapshot tool — zero-dependency Windows window tracker.

Отвечает на "что на моём экране / что открыто / где активное окно"
без pygetwindow/pywinauto — только ctypes (core.window_tracker).
На не-Windows возвращает пустой список.
"""
from __future__ import annotations

try:
    from core.window_tracker import get_active_window, snapshot, summarize
except Exception:  # pragma: no cover — инструмент просто недоступен
    snapshot = None  # type: ignore

    def get_active_window():  # type: ignore
        return None

    def summarize(windows):  # type: ignore
        return "Трекер окон недоступен на этой платформе."


def window_snapshot(parameters=None, **_) -> str:
    """Снимок окон: all | active | summary."""
    parameters = parameters or {}
    mode = str(parameters.get("mode", "all") or "all").strip().lower()
    try:
        limit = int(parameters.get("limit", 20))
    except Exception:
        limit = 20
    if snapshot is None:
        return "Трекер окон недоступен."
    try:
        if mode == "active":
            w = get_active_window()
            if not w:
                return "Активного окна не вижу."
            return f"{w['title']} ({w['exe']}) [{w['state']}]"
        windows = snapshot()[: max(1, min(50, int(limit)))]
        if mode == "summary":
            return summarize(windows)
        if not windows:
            return "Открытых окон не вижу."
        lines = []
        for w in windows:
            mark = "*" if w.get("active") else " "
            lines.append(
                f"{mark} {w['title'][:80]} ({w['exe']}) [{w['state']}]"
            )
        return f"Открыто: {len(snapshot())}. Активные сверху:\n" + "\n".join(lines)
    except Exception as e:
        return f"window_snapshot failed: {e}"


TOOL = {
    "name": "window_snapshot",
    "description": (
        "Lists open windows/apps on this Windows PC without extra dependencies. "
        "Use when the user asks what is open, what is on screen, where is a window, "
        "or which window is active."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "mode": {
                "type": "STRING",
                "description": "all (default) | active | summary",
            },
            "limit": {
                "type": "INTEGER",
                "description": "Max windows to list (1-50, default 20)",
            },
        },
        "required": [],
    },
    "handler": window_snapshot,
}
