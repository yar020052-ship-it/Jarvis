"""JARVIS: трекер состояния открытых окон / приложений (Windows).

Только стандартная библиотека (ctypes). Без зависимостей.
Дает JARVIS ответы на "что на моём экране", "что открыто", "куда делось окно".

Использование:
    from core.window_tracker import snapshot, get_active_window, WindowWatcher

    snap = snapshot()              # список dict: hwnd, title, exe, pid, state, rect, active
    active = get_active_window()   # текущее активное окно
    watcher = WindowWatcher(interval=1.0, on_event=print)
    watcher.start(); ...; watcher.stop()
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import sys
import threading
import time

_IS_WIN = sys.platform == "win32"

if _IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    _EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    user32.EnumWindows.argtypes = [_EnumWindowsProc, wt.LPARAM]
    user32.EnumWindows.restype = wt.BOOL
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsWindowVisible.restype = wt.BOOL
    user32.IsIconic.argtypes = [wt.HWND]
    user32.IsIconic.restype = wt.BOOL
    user32.IsZoomed.argtypes = [wt.HWND]
    user32.IsZoomed.restype = wt.BOOL
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.GetWindowRect.restype = wt.BOOL


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", wt.POINT),
        ("ptMaxPosition", wt.POINT),
        ("rcNormalPosition", wt.RECT),
    ]


if _IS_WIN:
    user32.GetWindowPlacement.argtypes = [wt.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
    user32.GetWindowPlacement.restype = wt.BOOL

# showCmd: 1=normal, 2=minimized, 3=maximized, 0=hidden/unknown
SW = {0: "hidden", 1: "normal", 2: "minimized", 3: "maximized"}


def _exe_for_pid(pid: int) -> str:
    """Имя exe по PID без сторонних зависимостей (psapi.GetModuleBaseNameW)."""
    if not _IS_WIN or not pid:
        return ""
    try:
        h = kernel32.OpenProcess(0x0410, False, pid)  # QUERY_INFORMATION|VM_READ
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            psapi.GetModuleBaseNameW.argtypes = [
                wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
            psapi.GetModuleBaseNameW.restype = wt.DWORD
            n = psapi.GetModuleBaseNameW(h, None, buf, 1024)
            if n:
                return buf.value
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        pass
    return ""


def _window_title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value.strip()


def _window_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    n = user32.GetClassNameW(hwnd, buf, 256)
    return buf.value if n > 0 else ""


def _window_rect(hwnd) -> dict:
    r = wt.RECT()
    try:
        if user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return {"x": r.left, "y": r.top,
                    "w": r.right - r.left, "h": r.bottom - r.top}
    except Exception:
        pass
    return {"x": 0, "y": 0, "w": 0, "h": 0}


def _window_show_cmd(hwnd) -> int:
    try:
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            return int(wp.showCmd)
    except Exception:
        pass
    return 1


def snapshot(include_hidden: bool = False, min_title_len: int = 1) -> list[dict]:
    """Снимок открытых окон верхнего уровня.

    Возвращает список dict:
      hwnd, title, exe, pid, cls, state(normal|minimized|maximized|hidden),
      rect{x,y,w,h}, active(bool)
    Отсортировано: активное первым, затем по title.
    """
    if not _IS_WIN:
        return []
    try:
        fg = user32.GetForegroundWindow()
    except Exception:
        fg = 0
    out: list[dict] = []

    @_EnumWindowsProc
    def cb(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd) and not include_hidden:
                return True
            title = _window_title(hwnd)
            if len(title) < min_title_len:
                return True
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid = int(pid.value)
            if user32.IsIconic(hwnd):
                state = "minimized"
            elif user32.IsZoomed(hwnd):
                state = "maximized"
            else:
                cmd = _window_show_cmd(hwnd)
                state = SW.get(cmd, "normal")
                if not user32.IsWindowVisible(hwnd):
                    state = "hidden"
            out.append({
                "hwnd": int(hwnd),
                "title": title,
                "exe": _exe_for_pid(pid),
                "pid": pid,
                "cls": _window_class(hwnd),
                "state": state,
                "rect": _window_rect(hwnd),
                "active": int(hwnd) == int(fg),
            })
        except Exception:
            pass
        return True

    user32.EnumWindows(cb, 0)
    out.sort(key=lambda w: (not w["active"], w["title"].lower()))
    return out


def get_active_window() -> dict | None:
    """Активное (фокусное) окно или None."""
    if not _IS_WIN:
        return None
    fg = user32.GetForegroundWindow()
    if not fg:
        return None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    pid = int(pid.value)
    if user32.IsIconic(fg):
        state = "minimized"
    elif user32.IsZoomed(fg):
        state = "maximized"
    else:
        state = SW.get(_window_show_cmd(fg), "normal")
    return {
        "hwnd": int(fg),
        "title": _window_title(fg),
        "exe": _exe_for_pid(pid),
        "pid": pid,
        "cls": _window_class(fg),
        "state": state,
        "rect": _window_rect(fg),
        "active": True,
    }


def diff_snapshots(old: list[dict], new: list[dict]) -> list[dict]:
    """Разница двух снимков -> события для JARVIS.

    События: opened, closed, focus, minimized, restored, renamed.
    Каждое: {type, hwnd, title, exe, ...}
    """
    o = {w["hwnd"]: w for w in old}
    n = {w["hwnd"]: w for w in new}
    events: list[dict] = []
    for hwnd, w in n.items():
        if hwnd not in o:
            events.append({"type": "opened", **w})
        else:
            p = o[hwnd]
            if w["title"] != p["title"]:
                events.append({"type": "renamed", "old_title": p["title"], **w})
            if w["state"] != p["state"]:
                if w["state"] == "minimized":
                    events.append({"type": "minimized", **w})
                elif p["state"] == "minimized" and w["state"] != "minimized":
                    events.append({"type": "restored", **w})
                else:
                    events.append({"type": "state_changed",
                                   "old_state": p["state"], **w})
            if w["active"] and not p["active"]:
                events.append({"type": "focus", **w})
    for hwnd, w in o.items():
        if hwnd not in n:
            events.append({"type": "closed", **w})
    return events


def summarize(windows: list[dict]) -> str:
    """Короткая текстовая сводка для голосового ответа JARVIS."""
    if not windows:
        return "Открытых окон не вижу."
    active = next((w for w in windows if w["active"]), windows[0])
    names = [w["title"] for w in windows[:5]]
    s = f"Открыто окон: {len(windows)}. Активно: {active['title']} ({active['exe']})."
    if names:
        s += " Топ: " + "; ".join(names) + "."
    return s


class WindowWatcher:
    """Фоновый опрос состояния окон. Дергает on_event(event) при изменениях."""

    def __init__(self, interval: float = 1.0, on_event=None,
                 include_hidden: bool = False):
        self.interval = max(0.2, float(interval))
        self.on_event = on_event or (lambda e: None)
        self.include_hidden = include_hidden
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last: list[dict] = []

    def start(self) -> "WindowWatcher":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self.last = snapshot(include_hidden=self.include_hidden)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                cur = snapshot(include_hidden=self.include_hidden)
                for ev in diff_snapshots(self.last, cur):
                    try:
                        self.on_event(ev)
                    except Exception:
                        pass
                self.last = cur
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def state(self) -> list[dict]:
        return list(self.last)


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="JARVIS window tracker")
    ap.add_argument("--once", action="store_true", help="один снимок JSON")
    ap.add_argument("--active", action="store_true", help="только активное окно")
    ap.add_argument("--watch", action="store_true", help="следить за изменениями")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--summary", action="store_true", help="текстовая сводка")
    a = ap.parse_args()
    if a.active:
        print(json.dumps(get_active_window(), ensure_ascii=False, indent=1))
    elif a.watch:
        print("watch: Ctrl+C для остановки", flush=True)
        w = WindowWatcher(interval=a.interval,
                          on_event=lambda e: print(json.dumps(e, ensure_ascii=False)))
        w.start()
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            w.stop()
    else:
        snap = snapshot()
        if a.summary:
            print(summarize(snap))
        else:
            print(json.dumps(snap, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    _cli()
