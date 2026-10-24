"""Windows idle detection for gated background JARVIS improvement.

Only the OS idle duration is read. No keyboard contents, screenshots, active
window titles, or per-application usage are collected.
"""
from __future__ import annotations

import ctypes


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input, or None if unavailable."""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        # Tick count and LASTINPUTINFO are DWORD values and wrap every ~49 days.
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        elapsed_ms = (now - info.dwTime) & 0xFFFFFFFF
        return max(0.0, elapsed_ms / 1000.0)
    except Exception:
        return None


def is_idle(threshold_seconds: float = 300.0) -> bool:
    """Conservatively report idle only when Windows confirms the threshold."""
    elapsed = idle_seconds()
    return elapsed is not None and elapsed >= max(60.0, float(threshold_seconds))
