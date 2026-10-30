"""Multi-monitor support for JARVIS — detect and manage multiple displays.

Provides information about connected monitors and allows moving windows
between screens.
"""

import ctypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def list_monitors(parameters=None, response=None, player=None, session_memory=None) -> str:
    """List connected monitors."""
    try:
        user32 = ctypes.windll.user32
        monitors = []

        def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_ulong),
                    ("rcMonitor", ctypes.c_long * 4),
                    ("rcWork", ctypes.c_long * 4),
                    ("dwFlags", ctypes.c_ulong),
                ]
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            user32.GetMonitorInfoW(hMonitor, ctypes.byref(info))
            left, top, right, bottom = info.rcMonitor
            w, h = right - left, bottom - top
            primary = bool(info.dwFlags & 1)
            monitors.append(f"  • Монитор {len(monitors)+1}: {w}x{h} {'(основной)' if primary else ''} — позиция ({left}, {top})")
            return True

        MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_long * 4), ctypes.c_double)
        user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(callback), 0)

        if not monitors:
            return "Мониторы не обнаружены."
        return f"Обнаружено мониторов: {len(monitors)}\n" + "\n".join(monitors)
    except Exception as e:
        return f"Ошибка получения информации о мониторах: {e}"


def move_window(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Move the active window to a specific monitor."""
    params = parameters or {}
    monitor = int(params.get("monitor", 1))

    try:
        import win32gui
        import win32con
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "Активное окно не найдено."
        # Get monitor info
        monitors = []
        def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.c_long * 4),
                            ("rcWork", ctypes.c_long * 4), ("dwFlags", ctypes.c_ulong)]
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            ctypes.windll.user32.GetMonitorInfoW(hMonitor, ctypes.byref(info))
            monitors.append((info.rcMonitor[0], info.rcMonitor[1], info.rcMonitor[2] - info.rcMonitor[0], info.rcMonitor[3] - info.rcMonitor[1]))
            return True
        MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_long * 4), ctypes.c_double)
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(callback), 0)

        if monitor < 1 or monitor > len(monitors):
            return f"Монитор {monitor} не найден. Доступно: {len(monitors)}."

        x, y, w, h = monitors[monitor - 1]
        win32gui.MoveWindow(hwnd, x + 50, y + 50, w - 100, h - 100, True)
        return f"Окно перемещено на монитор {monitor}."
    except ImportError:
        return "Модуль win32gui не установлен. Установите: pip install pywin32"
    except Exception as e:
        return f"Ошибка перемещения окна: {e}"


TOOL = {
    "name": "multi_monitor",
    "description": "Detect connected monitors and move windows between screens.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list (list monitors) or move (move active window)",
            },
            "monitor": {
                "type": "INTEGER",
                "description": "Monitor number to move window to (for move action)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        list_monitors(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "list").lower() == "list"
        else move_window(parameters, response, player, session_memory)
    ),
}