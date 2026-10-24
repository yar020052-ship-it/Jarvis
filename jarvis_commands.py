"""JARVIS Commands — командный центр в духе Luxify Assistant.

Сердце голосового управления: одна фраза — цепочка действий.
Команды живут в `.jarvis/commands/library.json` (коллекции/папки/команды),
готовые паки — в `jarvis_packs.py`, ставятся одной командой.

Что умеет:
  * фразы: точные, с «словами до/после», нечёткие (опечатки распознавания),
    с параметром {query} («загугли …», «открой сайт …», «скажи …»);
  * цепочки: «сверни все окна и сделай тише» — несколько команд одной фразой;
  * 35+ действий: приложения, сайты, клавиши, мышь, громкость, питание,
    окна, буфер обмена, файлы, скриншоты, Telegram, голос, вопрос к ИИ;
  * подтверждение опасных действий (голосом / кнопкой в Telegram / --yes);
  * импорт/экспорт (команда/папка/вся библиотека), клонирование, вкл/выкл;
  * «Создать с помощью ИИ» — описание словами → готовая команда (ai);
  * журнал запусков `.jarvis/commands/history.jsonl`.

CLI:
    python jarvis_commands.py list [--folder ИМЯ]
    python jarvis_commands.py match "сделай тише"        # какая команда сработает
    python jarvis_commands.py run "сверни все окна"      # выполнить
    python jarvis_commands.py run "выключи компьютер" --yes
    python jarvis_commands.py packs / pack install windows / pack remove windows
    python jarvis_commands.py enable c_… / disable c_… / rm c_…
    python jarvis_commands.py export out.json / import out.json
    python jarvis_commands.py ai "открой Steam и Discord, звук 50"
    python jarvis_commands.py selftest

Из кода:
    from jarvis_commands import ExecContext, run_phrase, load_library
    res = run_phrase("тише", ExecContext(root=ROOT))
    print(res.reply)
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import difflib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, ".jarvis", "commands")
LIB_PATH = os.path.join(DATA_DIR, "library.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")
SCREENS_DIR = os.path.join(ROOT, "screenshots")

VERSION = 1
ACCEPT_SCORE = 0.74          # порог уверенности совпадения фразы
_IS_WIN = sys.platform == "win32"

if _IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # правильные типы для clipboard/памяти
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SendInput.restype = wt.UINT


class CommandError(RuntimeError):
    """Ошибка шага команды (понятная человеку)."""


# --------------------------------------------------------------------------- #
#  Текст: нормализация и замены (частые ошибки распознавания)
# --------------------------------------------------------------------------- #

_REPLACEMENTS = [
    (r"\bютьюб\b|\bютюб\b|\bютубе?\b|\byoutube\b", "ютуб"),
    (r"\bтелега\b|\bтелеграмме?\b|\btelegram\b", "телеграм"),
    (r"\bгромкость?\b", "громко"),
    (r"\bджарвис\b|\bджарвиз\b|\bжарвис\b|\bjarvis\b", ""),
    (r"\bпотрубуй\b|\bпопробуй\b", ""),
    (r"\bпожалуйста\b", ""),
    (r"\bне мог бы ты\b|\bможешь\b|\bможно\b", ""),
]


def normalize(text: str) -> str:
    """Нормализация: регистр, ё→е, пунктуация, пробелы, известные оговорки."""
    s = str(text or "").strip().lower().replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я\-+{}\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for pat, rep in _REPLACEMENTS:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> list[str]:
    return [t for t in s.split() if t]


# --------------------------------------------------------------------------- #
#  Универсальные хелперы Windows
# --------------------------------------------------------------------------- #

def _run_hidden(cmd: list[str], timeout: float = 20.0) -> tuple[int, str]:
    """Запуск без мелькания консоли; возвращает (rc, вывод)."""
    flags = 0x08000000 if _IS_WIN else 0  # CREATE_NO_WINDOW
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=flags)
        out = (r.stdout or b"").decode("utf-8", "replace") + \
              (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, "таймаут"
    except FileNotFoundError as e:
        return 127, str(e)


def _run_ps(script: str, timeout: float = 30.0) -> tuple[int, str]:
    """PowerShell через base64 (без проблем с кавычками)."""
    import base64
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return _run_hidden(["powershell", "-NoProfile", "-NonInteractive",
                        "-EncodedCommand", enc], timeout=timeout)


def _ps_quote(text: str) -> str:
    return str(text).replace("'", "''")


# ---------------- SendInput (клавиатура/мышь) ----------------

ULONG_PTR = ctypes.c_size_t
INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD),
                ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


KEYMAP: dict[str, int] = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "space": 0x20, "spacebar": 0x20, "пробел": 0x20,
    "backspace": 0x08, "bksp": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "ins": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "capslock": 0x14, "printscreen": 0x2C, "prtsc": 0x2C,
    "volumeup": 0xAF, "voldown": 0xAE, "volumemute": 0xAD,
    "playpause": 0xB3, "play": 0xB3, "pause": 0xB3,
    "nexttrack": 0xB0, "next": 0xB0, "prevtrack": 0xB1, "prev": 0xB1,
    "stopmedia": 0xB2,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "ctrl": 0x11, "control": 0x11,
    "alt": 0x12, "shift": 0x10, "menu": 0x5D,
    "semicolon": 0xBA, "equals": 0xBB, "comma": 0xBC, "minus": 0xBD,
    "period": 0xBE, "slash": 0xBF, "grave": 0xC0, "lbracket": 0xDB,
    "backslash": 0xDC, "rbracket": 0xDD, "quote": 0xDE,
}
for _i in range(1, 25):
    KEYMAP[f"f{_i}"] = 0x6F + _i
for _d in "0123456789":
    KEYMAP[_d] = 0x30 + int(_d)
for _c in "abcdefghijklmnopqrstuvwxyz":
    KEYMAP[_c] = ord(_c.upper())

MODS = {"win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "ctrl": 0x11,
        "control": 0x11, "alt": 0x12, "shift": 0x10}


def _send_inputs(inputs: list) -> None:
    if not inputs or not _IS_WIN:
        return
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(_INPUT))


def _key_input(vk: int, up: bool = False,
               extended: bool = False) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki = _KEYBDINPUT(vk, 0,
                           (KEYEVENTF_KEYUP if up else 0)
                           | (KEYEVENTF_EXTENDEDKEY if extended else 0),
                           0, 0)
    return inp


def _unicode_input(ch: str, up: bool = False) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki = _KEYBDINPUT(0, ord(ch),
                           KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0),
                           0, 0)
    return inp


def _vk_for(char: str) -> int:
    """VK-код произвольного символа (для - = [ ] ; , . / и т.п.)."""
    res = user32.VkKeyScanW(char)
    if res == -1:
        return 0
    return res & 0xFF


def press_key(key: str, times: int = 1) -> None:
    """Одно нажатие (или несколько) по имени клавиши."""
    k = str(key or "").strip().lower()
    vk = KEYMAP.get(k)
    if not vk and len(k) == 1:
        vk = _vk_for(k) or (ord(k.upper()) if k.isalpha() else 0)
    if not vk:
        raise CommandError(f"не знаю клавишу «{key}»")
    times = max(1, min(int(times or 1), 100))
    seq = []
    for _ in range(times):
        seq.append(_key_input(vk))
        seq.append(_key_input(vk, up=True))
    _send_inputs(seq)


def hotkey(keys: str) -> None:
    """Комбинация вида 'win+d', 'ctrl+shift+p', 'alt+f4'."""
    parts = [p.strip().lower() for p in str(keys or "").split("+") if p.strip()]
    if not parts:
        raise CommandError("пустая комбинация клавиш")
    vks: list[int] = []
    for p in parts:
        vk = MODS.get(p) or KEYMAP.get(p)
        if not vk and len(p) == 1:
            vk = _vk_for(p) or (ord(p.upper()) if p.isalpha() else 0)
        if not vk:
            raise CommandError(f"не знаю клавишу «{p}» в «{keys}»")
        vks.append(vk)
    seq = [_key_input(v) for v in vks]
    seq += [_key_input(v, up=True) for v in reversed(vks)]
    _send_inputs(seq)


def type_text(text: str) -> None:
    """Печать текста (любой юникод, не зависит от раскладки)."""
    seq = []
    for ch in str(text):
        if ch == "\n":
            seq.append(_key_input(0x0D))
            seq.append(_key_input(0x0D, up=True))
            continue
        seq.append(_unicode_input(ch))
        seq.append(_unicode_input(ch, up=True))
    # отправляем порциями, чтобы не переполнить буфер SendInput
    for i in range(0, len(seq), 64):
        _send_inputs(seq[i:i + 64])
        time.sleep(0.01)


def _mouse_input(flags: int, dx: int = 0, dy: int = 0,
                 data: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.u.mi = _MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    return inp


def mouse_move(x: int, y: int) -> None:
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    ax = int(int(x) * 65535 / max(1, sw - 1))
    ay = int(int(y) * 65535 / max(1, sh - 1))
    _send_inputs([_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay)])


def mouse_click(button: str = "left", double: bool = False,
                x: Optional[int] = None, y: Optional[int] = None) -> None:
    if x is not None and y is not None:
        mouse_move(int(x), int(y))
        time.sleep(0.05)
    btn = str(button or "left").lower()
    down, up = {"left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
                "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
                "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
                }[btn if btn in ("left", "right", "middle") else "left"]
    clicks = 2 if double else 1
    seq = []
    for _ in range(clicks):
        seq.append(_mouse_input(down))
        seq.append(_mouse_input(up))
        if clicks > 1:
            time.sleep(0.03)
    _send_inputs(seq)


def mouse_scroll(ticks: int) -> None:
    """Прокрутка колесом: + вверх, - вниз (шаг = 120)."""
    ticks = int(ticks or 0)
    step = 120 if ticks >= 0 else -120
    for _ in range(abs(ticks)):
        _send_inputs([_mouse_input(MOUSEEVENTF_WHEEL, 0, 0, step)])
        time.sleep(0.01)


def mouse_drag(x1: int, y1: int, x2: int, y2: int) -> None:
    mouse_move(int(x1), int(y1))
    time.sleep(0.1)
    _send_inputs([_mouse_input(MOUSEEVENTF_LEFTDOWN)])
    time.sleep(0.1)
    steps = 12
    for i in range(1, steps + 1):
        mouse_move(int(x1 + (int(x2) - int(x1)) * i / steps),
                   int(y1 + (int(y2) - int(y1)) * i / steps))
        time.sleep(0.02)
    _send_inputs([_mouse_input(MOUSEEVENTF_LEFTUP)])


# ---------------- буфер обмена ----------------

def clipboard_set(text: str) -> bool:
    if not _IS_WIN:
        return False
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    data = (str(text) + "\x00").encode("utf-16-le")
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return False
    p = kernel32.GlobalLock(h)
    if not p:
        return False
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(CF_UNICODETEXT, h)
        return True
    finally:
        user32.CloseClipboard()


def clipboard_get() -> str:
    if not _IS_WIN:
        return ""
    CF_UNICODETEXT = 13
    if not user32.OpenClipboard(None):
        return ""
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


# ---------------- громкость ----------------

def _volume_pycaw(level: Optional[int] = None,
                  mute: Optional[bool] = None) -> bool:
    """Точная громкость через pycaw, если он установлен."""
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        dev = AudioUtilities.GetSpeakers()
        iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(iface, POINTER(IAudioEndpointVolume))
        if mute is not None:
            vol.SetMute(1 if mute else 0, None)
        if level is not None:
            vol.SetMasterVolumeLevelScalar(max(0, min(100, int(level))) / 100.0,
                                           None)
        return True
    except Exception:
        return False


def volume_up(times: int = 1) -> None:
    press_key("volumeup", times)


def volume_down(times: int = 1) -> None:
    press_key("voldown", times)


def volume_mute() -> None:
    press_key("volumemute", 1)


def volume_set(level: int) -> None:
    """0–100. Точная — через pycaw; иначе приблизительно (клавишами по 2%)."""
    level = max(0, min(100, int(level)))
    if _volume_pycaw(level=level):
        return
    # приближение: сбрасываем в 0 и поднимаем до нужного уровня
    press_key("voldown", 50)
    up_times = int(round(level / 2))
    if up_times:
        press_key("volumeup", up_times)


def volume_status() -> Optional[int]:
    """Текущая громкость в % (pycaw) или None."""
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        dev = AudioUtilities.GetSpeakers()
        iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(iface, POINTER(IAudioEndpointVolume))
        return int(round(vol.GetMasterVolumeLevelScalar() * 100))
    except Exception:
        return None


# ---------------- питание и системные ----------------

def lock_workstation() -> None:
    if not user32.LockWorkStation():
        raise CommandError("не удалось заблокировать ПК")


def monitor_off() -> None:
    # WM_SYSCOMMAND / SC_MONITORPOWER (2 = выключить)
    user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)


def sleep_pc() -> None:
    if not ctypes.windll.powrprof.SetSuspendState(0, 1, 0):
        raise CommandError("система отклонила переход в сон")


def hibernate_pc() -> None:
    if not ctypes.windll.powrprof.SetSuspendState(1, 1, 0):
        raise CommandError("гибернация отключена в системе")


def _exit_windows(flags: int) -> None:
    user32.ExitWindowsEx(flags, 0)


def shutdown_pc() -> None:
    _exit_windows(0x00000008 | 0x00000004)   # POWEROFF | FORCEIFHUNG


def restart_pc() -> None:
    _exit_windows(0x00000002 | 0x00000004)   # REBOOT | FORCEIFHUNG


def logoff_pc() -> None:
    _exit_windows(0x00000004)                # LOGOFF | FORCEIFHUNG


# ---------------- запуск приложений и сайтов ----------------

def launch_app(target: str, args: Optional[list] = None,
               workdir: Optional[str] = None) -> None:
    target = str(target or "").strip()
    if not target:
        raise CommandError("не указано, что запускать")
    expanded = os.path.expandvars(os.path.expanduser(target))
    quoted = f'"{expanded}"' if " " in expanded and not expanded.startswith('"') \
        else expanded
    extra = "".join(f' "{a}"' for a in (args or []))
    cmd = f'start "" {quoted}{extra}'
    try:
        subprocess.Popen(cmd, shell=True, cwd=workdir or None,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as e:
        raise CommandError(f"не удалось запустить «{target}»: {e}") from e


def open_url(url: str) -> None:
    u = str(url or "").strip()
    if not u:
        raise CommandError("пустой адрес")
    if not re.match(r"^[a-z]+://", u, re.I):
        u = "https://" + u
    # кодируем пробелы/кириллицу, не трогая служебные символы URL
    u = urllib.parse.quote(u, safe=":/?#[]@!$&'()*+,;=%")
    launch_app(u)


SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={q}",
    "гугл": "https://www.google.com/search?q={q}",
    "yandex": "https://yandex.ru/search/?text={q}",
    "яндекс": "https://yandex.ru/search/?text={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "ютуб": "https://www.youtube.com/results?search_query={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "ddg": "https://duckduckgo.com/?q={q}",
}


def web_search(query: str, engine: str = "google") -> str:
    tpl = SEARCH_ENGINES.get(str(engine or "google").lower(),
                             SEARCH_ENGINES["google"])
    url = tpl.format(q=urllib.parse.quote(str(query)))
    open_url(url)
    return url


# ---------------- окна ----------------

SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE = 6, 3, 9


def _find_window(match: str) -> Optional[dict]:
    try:
        from jarvis_windows import snapshot
    except ImportError:
        return None
    m = (match or "").strip().lower()
    if not m:
        from jarvis_windows import get_active_window
        return get_active_window()
    for w in snapshot():
        title = (w.get("title") or "").lower()
        exe = (w.get("exe") or "").lower()
        if m in title or m in exe:
            return w
    return None


def window_action(mode: str, match: str = "") -> str:
    w = _find_window(match)
    if not w:
        raise CommandError(f"окно «{match}» не найдено" if match
                           else "нет активного окна")
    hwnd = w["hwnd"]
    mode = (mode or "focus").lower()
    if mode == "focus":
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    elif mode == "minimize":
        user32.ShowWindow(hwnd, SW_MINIMIZE)
    elif mode == "maximize":
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        user32.SetForegroundWindow(hwnd)
    elif mode == "restore":
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    elif mode == "close":
        user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
    else:
        raise CommandError(f"не знаю действие с окном «{mode}»")
    return w.get("title") or ""


def wait_window(match: str, timeout: float = 30.0,
                interval: float = 0.7) -> str:
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline:
        w = _find_window(match)
        if w:
            return w.get("title") or match
        time.sleep(max(0.2, float(interval)))
    raise CommandError(f"окно «{match}» не появилось за {int(timeout)} сек")


def active_window_title() -> str:
    w = _find_window("")
    return (w or {}).get("title", "")


# ---------------- файлы, скриншоты, toast ----------------

def create_folder(path: str) -> str:
    p = os.path.expandvars(os.path.expanduser(str(path)))
    os.makedirs(p, exist_ok=True)
    return p


def write_text_file(path: str, text: str, mode: str = "append") -> str:
    p = os.path.expandvars(os.path.expanduser(str(path)))
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w" if str(mode).lower() == "overwrite" else "a",
              encoding="utf-8") as f:
        f.write(str(text))
        if not str(text).endswith("\n"):
            f.write("\n")
    return p


def screenshot(path: Optional[str] = None) -> str:
    try:
        from PIL import ImageGrab
    except ImportError as e:
        raise CommandError("нет Pillow (pip install pillow)") from e
    if not path:
        os.makedirs(SCREENS_DIR, exist_ok=True)
        path = os.path.join(SCREENS_DIR,
                            f"screen_{datetime.now():%Y%m%d_%H%M%S}.png")
    try:
        img = ImageGrab.grab(all_screens=True)
    except TypeError:
        img = ImageGrab.grab()
    img.save(path)
    return path


def toast(title: str, text: str = "") -> bool:
    """Всплывающее уведомление Windows (best effort)."""
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "$tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$texts = $tpl.GetElementsByTagName('text'); "
        f"$texts.Item(0).AppendChild($tpl.CreateTextNode('{_ps_quote(title)}')) | Out-Null; "
        f"$texts.Item(1).AppendChild($tpl.CreateTextNode('{_ps_quote(text)}')) | Out-Null; "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('JARVIS').Show([Windows.UI.Notifications.ToastNotification]::new($tpl))"
    )
    rc, _ = _run_ps(script, timeout=20)
    return rc == 0


# --------------------------------------------------------------------------- #
#  Библиотека команд
# --------------------------------------------------------------------------- #

def _new_id(prefix: str = "c") -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


STOP_TYPES = {"shutdown", "restart", "logoff", "sleep", "hibernate",
              "monitor_off", "lock", "remove", "format"}


def _step_is_risky(step: dict) -> bool:
    if str(step.get("type", "")).lower() in STOP_TYPES:
        return True
    if str(step.get("type", "")).lower() in ("cmd", "powershell"):
        return bool(step.get("dangerous"))
    return False


@dataclass
class Command:
    id: str
    name: str
    phrases: list[str]
    steps: list[dict]
    folder: str = ""              # id папки
    enabled: bool = True
    confirm: bool = False         # спрашивать подтверждение перед запуском
    reply: str = ""               # что сказать голосом после выполнения
    note: str = ""
    origin: dict = field(default_factory=dict)   # {"pack": "...", "key": "..."}
    modified: bool = False
    created: str = ""

    @staticmethod
    def from_dict(d: dict) -> "Command":
        return Command(
            id=str(d.get("id") or _new_id()),
            name=str(d.get("name") or ""),
            phrases=[str(p) for p in (d.get("phrases") or [])],
            steps=list(d.get("steps") or []),
            folder=str(d.get("folder") or ""),
            enabled=bool(d.get("enabled", True)),
            confirm=bool(d.get("confirm", False)),
            reply=str(d.get("reply") or ""),
            note=str(d.get("note") or ""),
            origin=dict(d.get("origin") or {}),
            modified=bool(d.get("modified", False)),
            created=str(d.get("created") or ""),
        )

    def to_dict(self) -> dict:
        d = {"id": self.id, "name": self.name, "phrases": self.phrases,
             "steps": self.steps, "folder": self.folder,
             "enabled": self.enabled, "confirm": self.confirm,
             "reply": self.reply, "note": self.note,
             "modified": self.modified}
        if self.origin:
            d["origin"] = self.origin
        if self.created:
            d["created"] = self.created
        return d


@dataclass
class Folder:
    id: str
    name: str
    parent: str = ""
    enabled: bool = True
    origin: dict = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "Folder":
        return Folder(id=str(d.get("id") or _new_id("f")),
                      name=str(d.get("name") or ""),
                      parent=str(d.get("parent") or ""),
                      enabled=bool(d.get("enabled", True)),
                      origin=dict(d.get("origin") or {}))

    def to_dict(self) -> dict:
        d = {"id": self.id, "name": self.name, "parent": self.parent,
             "enabled": self.enabled}
        if self.origin:
            d["origin"] = self.origin
        return d


class Library:
    def __init__(self, path: str = LIB_PATH):
        self.path = path
        self.folders: list[Folder] = []
        self.commands: list[Command] = []
        self.load()

    # ---- файл ----
    def load(self) -> None:
        self.folders, self.commands = [], []
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        self.folders = [Folder.from_dict(d) for d in data.get("folders", [])]
        self.commands = [Command.from_dict(d) for d in data.get("commands", [])]

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        data = {"version": VERSION,
                "updated": datetime.now().isoformat(timespec="seconds"),
                "folders": [f.to_dict() for f in self.folders],
                "commands": [c.to_dict() for c in self.commands]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---- папки ----
    def folder_by_id(self, fid: str) -> Optional[Folder]:
        return next((f for f in self.folders if f.id == fid), None)

    def folder_by_name(self, name: str) -> Optional[Folder]:
        n = (name or "").strip().lower()
        return next((f for f in self.folders if f.name.lower() == n), None)

    def ensure_folder(self, name: str, origin: Optional[dict] = None) -> Folder:
        f = self.folder_by_name(name)
        if f:
            return f
        f = Folder(id=_new_id("f"), name=name, origin=origin or {})
        self.folders.append(f)
        return f

    # ---- команды ----
    def command_by_id(self, cid: str) -> Optional[Command]:
        return next((c for c in self.commands if c.id == cid), None)

    def add(self, cmd: Command) -> Command:
        if not cmd.id:
            cmd.id = _new_id()
        if not cmd.created:
            cmd.created = datetime.now().isoformat(timespec="seconds")
        self.commands.append(cmd)
        return cmd

    def remove(self, cid: str) -> bool:
        before = len(self.commands)
        self.commands = [c for c in self.commands if c.id != cid]
        return len(self.commands) != before

    def enabled_commands(self) -> list[Command]:
        off_folders = {f.id for f in self.folders if not f.enabled}
        return [c for c in self.commands
                if c.enabled and c.folder not in off_folders]

    def find_by_name(self, name: str) -> Optional[Command]:
        n = (name or "").strip().lower()
        for c in self.commands:
            if c.name.lower() == n:
                return c
        for c in self.commands:
            if n in c.name.lower():
                return c
        return None

    # ---- импорт/экспорт/клонирование ----
    def export_payload(self, what: str = "") -> dict:
        """what: '' (всё) | id команды | id папки."""
        what = (what or "").strip()
        if not what:
            return {"version": VERSION, "kind": "library",
                    "folders": [f.to_dict() for f in self.folders],
                    "commands": [c.to_dict() for c in self.commands]}
        c = self.command_by_id(what)
        if c:
            return {"version": VERSION, "kind": "command",
                    "commands": [c.to_dict()]}
        f = self.folder_by_id(what)
        if f:
            inner = [c.to_dict() for c in self.commands if c.folder == f.id]
            return {"version": VERSION, "kind": "folder",
                    "folders": [f.to_dict()], "commands": inner}
        raise CommandError(f"не нашёл команду или папку «{what}»")

    def import_payload(self, payload: dict, folder: str = "") -> dict:
        """Слить импорт в библиотеку. Возвращает статистику."""
        added_c, added_f = 0, 0
        id_map: dict[str, str] = {}
        for fd in payload.get("folders", []):
            f = Folder.from_dict(fd)
            old_id = f.id
            f.id = _new_id("f")
            f.origin = {}
            self.folders.append(f)
            id_map[old_id] = f.id
            added_f += 1
        target_folder = folder or ""
        for cd in payload.get("commands", []):
            c = Command.from_dict(cd)
            c.id = _new_id()
            c.origin = {}
            c.modified = True
            if c.folder in id_map:
                c.folder = id_map[c.folder]
            elif target_folder:
                c.folder = target_folder
            self.commands.append(c)
            added_c += 1
        self.save()
        return {"commands": added_c, "folders": added_f}

    def clone(self, cid: str) -> Command:
        c = self.command_by_id(cid)
        if not c:
            raise CommandError(f"команда {cid} не найдена")
        new = Command.from_dict(c.to_dict())
        new.id = _new_id()
        new.name = c.name + " (копия)"
        new.origin = {}
        new.modified = True
        new.created = datetime.now().isoformat(timespec="seconds")
        self.commands.append(new)
        self.save()
        return new


# --------------------------------------------------------------------------- #
#  Сопоставление фразы -> команды (движок понимания)
# --------------------------------------------------------------------------- #

@dataclass
class Match:
    command: Command
    score: float
    args: dict = field(default_factory=dict)
    phrase: str = ""

    def to_dict(self) -> dict:
        return {"id": self.command.id, "name": self.command.name,
                "score": round(self.score, 3), "args": self.args,
                "folder": self.command.folder}


_ARG_RE = re.compile(r"\{(\w+)\}")


def _phrase_pattern(phrase: str) -> Optional[re.Pattern]:
    """Сравнить фразу с шаблоном {query} -> regex."""
    norm = normalize(phrase)
    if "{" not in phrase:
        return None
    parts = []
    last = 0
    for m in _ARG_RE.finditer(norm):
        parts.append(re.escape(norm[last:m.start()]))
        parts.append(r"(.+?)")
        last = m.end()
    parts.append(re.escape(norm[last:]))
    rx = "^\\s*" + "".join(parts).strip()
    return re.compile(rx + r"\s*$")


def score_against(phrase: str, text: str) -> tuple[float, dict]:
    """Насколько текст похож на фразу. (0..1, args)."""
    p = normalize(phrase)
    t = normalize(text)
    if not p or not t:
        return 0.0, {}
    rx = _phrase_pattern(phrase)
    if rx:
        m = rx.match(t)
        if m:
            return 0.96, {"query": m.group(1).strip()}
    if p == t:
        return 1.0, {}
    if re.search(rf"(?<![a-zа-я0-9]){re.escape(p)}(?![a-zа-я0-9])", t):
        # фраза целиком внутри текста: штраф за лишние слова
        extra = max(0, len(t) - len(p))
        return max(0.8, 0.95 - extra * 0.003), {}
    pt, tt = set(_tokens(p)), set(_tokens(t))
    if len(pt) >= 2 and pt.issubset(tt):
        return 0.88 - 0.01 * (len(tt) - len(pt)), {}
    ratio = difflib.SequenceMatcher(None, p, t).ratio()
    if ratio >= 0.7:
        return ratio * 0.92, {}
    return 0.0, {}


def split_chain(text: str) -> list[str]:
    """«сверни все окна и сделай тише» -> две фразы (по « и », «затем», «,»)."""
    parts = re.split(r"\s*(?:,\s*|\s+и\s+|\s+затем\s+|\s+потом\s+и\s+)\s*",
                     str(text or "").strip(), flags=re.I)
    return [p for p in (x.strip() for x in parts) if p]


def match_phrase(text: str, lib: Library,
                 threshold: float = ACCEPT_SCORE) -> list[Match]:
    """Подобрать команды под фразу. Пустой список — не поняли.

    Сначала пробуем разбить на цепочку «… и …» — если каждая часть уверенно
    совпала, выполняем в порядке произнесения. Иначе ищем по всей фразе.
    """
    parts = split_chain(text)
    if len(parts) >= 2:
        chain: list[Match] = []
        ok = True
        for part in parts:
            sub = match_phrase(part, lib, threshold)
            if not sub:
                ok = False
                break
            chain.append(sub[0])
        if ok:
            return chain
    results: list[Match] = []
    for c in lib.enabled_commands():
        best, best_args, best_phrase = 0.0, {}, ""
        for ph in c.phrases:
            sc, args = score_against(ph, text)
            if sc > best:
                best, best_args, best_phrase = sc, args, ph
        if best >= threshold:
            results.append(Match(c, best, best_args, best_phrase))
    if results:
        results.sort(key=lambda m: -m.score)
        return results[:3]
    return []


# --------------------------------------------------------------------------- #
#  Выполнение действий
# --------------------------------------------------------------------------- #

@dataclass
class ExecContext:
    """Контекст выполнения: подтверждение, события, озвучка, dry-run."""
    root: str = ROOT
    speak: bool = True            # озвучивать reply команды (TTS)
    dry_run: bool = False
    confirm_fn: Optional[Callable[[str], bool]] = None
    on_event: Optional[Callable[[str, str], None]] = None
    voice_profile: Optional[str] = None

    def event(self, kind: str, text: str) -> None:
        if self.on_event:
            try:
                self.on_event(kind, text)
            except Exception:
                pass

    def confirm(self, question: str) -> bool:
        """Спросить подтверждение (голос/кнопка/интерактивный ввод)."""
        if self.dry_run:
            return True
        if self.confirm_fn is not None:
            try:
                return bool(self.confirm_fn(question))
            except Exception:
                return False
        if sys.stdin and sys.stdin.isatty():
            try:
                ans = input(f"{question} [д/н]: ").strip().lower()
                return ans in ("д", "да", "y", "yes", "1")
            except (EOFError, KeyboardInterrupt):
                return False
        return False


_VAR_RX = re.compile(r"\{(clipboard|selection|date|time|user|active_window)\}")


def resolve_vars(text: str) -> str:
    """Подстановки: {clipboard}, {selection}, {date}, {time}, {user}, {active_window}."""
    s = str(text or "")

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        try:
            if key == "clipboard":
                return clipboard_get()
            if key == "selection":
                prev = clipboard_get()
                hotkey("ctrl+c")
                time.sleep(0.35)
                sel = clipboard_get()
                return sel or prev
            if key == "date":
                return datetime.now().strftime("%d.%m.%Y")
            if key == "time":
                return datetime.now().strftime("%H:%M")
            if key == "user":
                return os.environ.get("USERNAME", "")
            if key == "active_window":
                return active_window_title()
        except Exception:
            return ""
        return ""

    return _VAR_RX.sub(_sub, s) if "{" in s else s


def speak_line(text: str, ctx: ExecContext) -> bool:
    if not text:
        return False
    try:
        from jarvis_voice import say as voice_say
        return voice_say(resolve_vars(text), profile=ctx.voice_profile)
    except Exception:
        try:
            import jarvis_tts
            return jarvis_tts.speak(resolve_vars(text))
        except Exception:
            return False


def _agent_answer(prompt: str) -> str:
    """Короткий ответ ИИ для голоса (движок агента, только текст)."""
    sys.path.insert(0, ROOT)
    try:
        from jarvis_agent.backends import BackendError, build_backend
        from jarvis_agent.config import load_config
    except ImportError as e:
        return f"(агент недоступен: {e})"
    try:
        cfg = load_config(ROOT)
        backend = build_backend(cfg, ROOT)
    except BackendError as e:
        return f"(нет модели: {e})"
    if getattr(backend, "name", "") == "echo":
        return "(нет ключа модели — не могу ответить)"
    messages = [
        {"role": "system", "content":
            "Ты JARVIS. Отвечай кратко (1–3 предложения), по-русски, "
            "без markdown, списков и эмодзи — ответ будет озвучен голосом."},
        {"role": "user", "content": str(prompt)},
    ]
    try:
        msg = backend.complete(messages)
    except Exception as e:  # noqa: BLE001
        return f"(ошибка ИИ: {e})"
    return str(msg.get("content") or "").strip()


def agent_task(text: str) -> str:
    """Делегировать задачу полному агенту (jarvis_agent) в фоне."""
    cmd = [sys.executable, "-m", "jarvis_agent", "exec", str(text)]
    flags = 0x08000000 if _IS_WIN else 0
    try:
        subprocess.Popen(cmd, cwd=ROOT, creationflags=flags,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise CommandError(f"не удалось запустить агента: {e}") from e
    return "запущено в фоне"


def run_step(step: dict, ctx: ExecContext) -> str:
    """Выполнить один шаг. Возвращает короткий отчёт."""
    if not isinstance(step, dict):
        raise CommandError("шаг должен быть объектом (dict)")
    typ = str(step.get("type", "")).strip().lower()
    if not typ:
        raise CommandError("у шага нет типа (type)")

    if ctx.dry_run:
        return f"[dry-run] {typ}"

    if typ == "launch":
        launch_app(str(step.get("target", "")), step.get("args"),
                   step.get("workdir"))
        return f"запуск: {step.get('target')}"
    if typ == "url":
        open_url(resolve_vars(str(step.get("url", ""))))
        return f"сайт: {step.get('url')}"
    if typ == "site":
        q = str(step.get("query", "")).strip()
        open_url(q if "." in q and " " not in q else
                 f"https://www.google.com/search?q={urllib.parse.quote(q)}")
        return f"сайт: {q}"
    if typ == "file":
        p = os.path.expandvars(os.path.expanduser(str(step.get("path", ""))))
        if not os.path.exists(p):
            raise CommandError(f"файл не найден: {p}")
        os.startfile(p)  # noqa: S606 — только локальный пользователь
        return f"открыт: {p}"
    if typ == "cmd":
        cmd = resolve_vars(str(step.get("command", "")))
        rc, out = _run_hidden(["cmd", "/c", cmd],
                              timeout=float(step.get("timeout", 20)))
        ctx.event("cmd", f"{cmd} -> rc={rc}")
        if rc != 0:
            raise CommandError(f"команда вернула код {rc}: {out[:200]}")
        return f"cmd: {cmd[:60]}"
    if typ == "powershell":
        script = resolve_vars(str(step.get("script", "")))
        rc, out = _run_ps(script, timeout=float(step.get("timeout", 45)))
        if rc != 0:
            raise CommandError(f"PowerShell код {rc}: {out[:200]}")
        return "powershell выполнен"
    if typ == "hotkey":
        hotkey(str(step.get("keys", "")))
        return f"клавиши: {step.get('keys')}"
    if typ in ("click", "scroll", "drag"):
        try:
            import pyautogui
            sw, sh = pyautogui.size()
        except Exception:
            sw, sh = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)) if _IS_WIN else (0, 0)
        old_w = max(1, int(step.get("screen_width") or sw or 1))
        old_h = max(1, int(step.get("screen_height") or sh or 1))

        def point(x, y):
            px = round(int(x) * max(1, sw) / old_w)
            py = round(int(y) * max(1, sh) / old_h)
            return (max(0, min(max(0, sw - 1), px)),
                    max(0, min(max(0, sh - 1), py)))

        if typ == "click":
            x, y = point(step.get("x", 0), step.get("y", 0))
            mouse_click(str(step.get("button") or "left"),
                        bool(step.get("double", False)), x, y)
            return f"щелчок {step.get('button', 'left')} в {x},{y}"
        if typ == "scroll":
            x, y = point(step.get("x", 0), step.get("y", 0))
            mouse_move(x, y)
            mouse_scroll(int(step.get("ticks", 1) or 1))
            return "прокрутка мышью"
        x1, y1 = point(step.get("x1", 0), step.get("y1", 0))
        x2, y2 = point(step.get("x2", 0), step.get("y2", 0))
        mouse_drag(x1, y1, x2, y2)
        return "перетаскивание мышью"
    if typ == "key":
        press_key(str(step.get("key", "")), int(step.get("times", 1) or 1))
        return f"клавиша: {step.get('key')}"
    if typ == "media":
        key = {"play": "playpause", "pause": "playpause",
               "next": "nexttrack", "prev": "prevtrack",
               "stop": "stopmedia"}.get(str(step.get("key", "play")).lower(),
                                        "playpause")
        press_key(key)
        return f"медиа: {key}"
    if typ == "dictation":
        hotkey("win+h")
        return "диктовка Windows включена"
    if typ == "type":
        type_text(resolve_vars(str(step.get("text", ""))))
        return "текст напечатан"
    if typ == "paste":
        clipboard_set(resolve_vars(str(step.get("text", ""))))
        hotkey("ctrl+v")
        return "вставлено"
    if typ == "pause":
        sec = max(0.0, min(300.0, float(step.get("seconds", 1) or 1)))
        time.sleep(sec)
        return f"пауза {sec:g} сек"
    if typ == "volume":
        mode = str(step.get("mode", "up")).lower()
        if mode == "up":
            volume_up(int(step.get("times", 3) or 3))
        elif mode == "down":
            volume_down(int(step.get("times", 3) or 3))
        elif mode == "mute":
            volume_mute()
        elif mode == "set":
            volume_set(int(step.get("level", 50) or 0))
        elif mode == "get":
            v = volume_status()
            return f"громкость: {v if v is not None else '?'}%"
        else:
            raise CommandError(f"неизвестный режим громкости «{mode}»")
        return f"громкость: {mode}"
    if typ == "lock":
        lock_workstation()
        return "ПК заблокирован"
    if typ == "monitor_off":
        monitor_off()
        return "монитор выключен"
    if typ == "sleep":
        sleep_pc()
        return "переход в сон"
    if typ == "hibernate":
        hibernate_pc()
        return "гибернация"
    if typ in ("shutdown", "restart", "logoff"):
        words = {"shutdown": "выключить компьютер",
                 "restart": "перезагрузить компьютер",
                 "logoff": "выйти из аккаунта"}[typ]
        if not ctx.confirm(f"Подтвердите: {words}?"):
            raise CommandError("отменено пользователем (нет подтверждения)")
        {"shutdown": shutdown_pc, "restart": restart_pc,
         "logoff": logoff_pc}[typ]()
        return f"{typ}: команда отправлена"
    if typ == "minimize_all":
        hotkey("win+d")
        return "все окна свёрнуты"
    if typ == "alt_tab":
        hotkey("alt+tab")
        return "переключение окна"
    if typ == "task_view":
        hotkey("win+tab")
        return "представление задач"
    if typ == "window":
        title = window_action(str(step.get("mode", "focus")),
                              str(step.get("match", "")))
        return f"окно {step.get('mode')}: {title}"
    if typ == "wait_window":
        title = wait_window(str(step.get("match", "")),
                            float(step.get("timeout", 30) or 30))
        return f"дождались окна: {title}"
    if typ == "search":
        url = web_search(resolve_vars(str(step.get("query", ""))),
                         str(step.get("engine", "google")))
        return f"поиск: {url[:80]}"
    if typ == "folder":
        p = create_folder(resolve_vars(str(step.get("path", ""))))
        return f"папка: {p}"
    if typ == "writefile":
        p = write_text_file(resolve_vars(str(step.get("path", ""))),
                            resolve_vars(str(step.get("text", ""))),
                            str(step.get("mode", "append")))
        return f"записано: {p}"
    if typ == "clipboard_set":
        clipboard_set(resolve_vars(str(step.get("text", ""))))
        return "в буфере обмена"
    if typ == "clipboard_speak":
        text = clipboard_get().strip()
        if not text:
            raise CommandError("буфер обмена пуст")
        speak_line(text[:1500], ctx)
        return "буфер озвучен"
    if typ == "clipboard_type":
        text = clipboard_get()
        if not text:
            raise CommandError("буфер обмена пуст")
        type_text(text)
        return "буфер напечатан"
    if typ == "speak":
        ok = speak_line(str(step.get("text", "")), ctx)
        return "сказано" if ok else "TTS не сработал"
    if typ == "play":
        path = os.path.expandvars(os.path.expanduser(str(step.get("path", ""))))
        try:
            import jarvis_tts
            ok = jarvis_tts.play_file(path, wait=bool(step.get("wait", True)))
        except Exception:
            ok = False
        if not ok:
            raise CommandError(f"не удалось проиграть {path}")
        return f"звук: {os.path.basename(path)}"
    if typ == "toast":
        toast(str(step.get("title", "JARVIS")),
              resolve_vars(str(step.get("text", ""))))
        return "уведомление"
    if typ == "screenshot":
        p = screenshot(step.get("path"))
        return f"скриншот: {p}"
    if typ == "telegram":
        try:
            import jarvis_telegram as tg
            res = tg.send_message(resolve_vars(str(step.get("text", ""))))
        except Exception as e:  # noqa: BLE001
            raise CommandError(f"Telegram: {e}") from e
        if not res.get("ok"):
            raise CommandError(f"Telegram: {res.get('error')}")
        return "сообщение в Telegram"
    if typ == "ask":
        answer = _agent_answer(resolve_vars(str(step.get("prompt", ""))))
        if to_bool(step.get("speak", True)):
            speak_line(answer, ctx)
        else:
            ctx.event("ask", answer[:400])
        return answer[:160]
    if typ == "agent":
        info = agent_task(resolve_vars(str(step.get("task", ""))))
        if to_bool(step.get("speak", False)):
            speak_line("Задача передана Джарвису.", ctx)
        return f"агент: {info}"
    if typ == "if_window":
        match = normalize(str(step.get("match", "")))
        cur = normalize(active_window_title())
        branch = step.get("steps") if match and match in cur \
            else step.get("else_steps")
        if branch:
            _run_steps(list(branch), ctx)
        return f"условие окна «{match}»"
    if typ == "chain":
        ids = list(step.get("ids") or [])
        lib = Library(ctx.lib_path if hasattr(ctx, "lib_path") else LIB_PATH)
        for cid in ids:
            c = lib.command_by_id(str(cid))
            if c:
                execute_command(c, ctx)
        return f"цепочка: {len(ids)}"
    raise CommandError(f"неизвестное действие «{typ}»")


def to_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "да", "yes", "on")


def _run_steps(steps: list[dict], ctx: ExecContext) -> list[str]:
    """Выполнить шаги по порядку; остановиться на первой ошибке."""
    done: list[str] = []
    for i, step in enumerate(steps, 1):
        try:
            info = run_step(step, ctx)
        except CommandError:
            raise
        except Exception as e:  # noqa: BLE001
            raise CommandError(f"шаг {i} ({step.get('type')}): {e}") from e
        done.append(info)
        ctx.event("step", f"{i}/{len(steps)} {info}")
    return done


# --------------------------------------------------------------------------- #
#  Публичный запуск
# --------------------------------------------------------------------------- #

@dataclass
class RunResult:
    ok: bool
    matched: list[dict] = field(default_factory=list)
    reply: str = ""
    log: list[str] = field(default_factory=list)
    error: str = ""

    def text(self) -> str:
        if self.error:
            return f"Ошибка: {self.error}"
        if not self.matched:
            return "Команда не распознана."
        lines = [f"• {m['name']}" + (f" ({m.get('score')})" if m.get('score') else "")
                 for m in self.matched]
        if self.reply:
            lines.append(self.reply)
        return "\n".join(lines)


def execute_command(cmd: Command, ctx: ExecContext,
                    args: Optional[dict] = None) -> list[str]:
    """Выполнить команду (с учётом confirm и reply)."""
    if cmd.confirm:
        if not ctx.confirm(f"Подтвердите команду «{cmd.name}»?"):
            raise CommandError("отменено пользователем (нет подтверждения)")
    steps = list(cmd.steps or [])
    done = _run_steps(steps, ctx)
    if cmd.reply and ctx.speak:
        speak_line(cmd.reply, ctx)
    return done


def run_phrase(text: str, ctx: Optional[ExecContext] = None,
               lib: Optional[Library] = None,
               threshold: float = ACCEPT_SCORE) -> RunResult:
    """Главный вход: фраза -> подбор -> выполнение (в т.ч. цепочки).

    threshold можно поднять (например, 0.9), когда фраза пришла текстом и
    подходить к библиотеке надо строже, чем для голоса с оговорками.
    """
    ctx = ctx or ExecContext()
    lib = lib or Library()
    res = RunResult(ok=True)
    matches = match_phrase(text, lib, threshold)
    if not matches:
        res.ok = False
        res.error = "не понял фразу — нет подходящей команды"
        _history(text, None, False, res.error)
        return res
    for m in matches:
        try:
            steps = list(m.command.steps or [])
            # {query} из фразы подставляем в шаги без своего значения
            if m.args.get("query"):
                q = m.args["query"]
                for s in steps:
                    for k, v in list(s.items()):
                        if isinstance(v, str) and "{query}" in v:
                            s[k] = v.replace("{query}", q)
            info = execute_command(m.command, ctx)
            res.matched.append(m.to_dict())
            res.log += info
            if m.command.reply:
                res.reply = (res.reply + " " + m.command.reply).strip()
        except CommandError as e:
            res.ok = False
            res.error = f"«{m.command.name}»: {e}"
            _history(text, m.command.name, False, str(e))
            return res
    _history(text, "\n".join(m["name"] for m in res.matched)[:300], True, "")
    return res


def _history(phrase: str, matched: Optional[str], ok: bool, error: str) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "t": datetime.now().isoformat(timespec="seconds"),
                "phrase": str(phrase)[:300], "command": matched,
                "ok": ok, "error": str(error)[:300],
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def history(limit: int = 20) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return rows[-limit:][::-1]


# --------------------------------------------------------------------------- #
#  «Создать с помощью ИИ»
# --------------------------------------------------------------------------- #

AI_SCHEMA_HINT = """{
  "name": "короткое название",
  "phrases": ["фраза 1", "фраза 2"],
  "steps": [ {"type": "launch", "target": "steam"} , ... ],
  "reply": "что сказать голосом (можно пусто)",
  "note": "пояснение"
}
Доступные типы шагов (type) и их поля:
  launch: target (имя exe или путь), args[], workdir
  url: url       site: query        file: path
  cmd: command   powershell: script
  hotkey: keys("win+d","ctrl+shift+p")   key: key,times   type: text   paste: text
  click: x,y,button(left|right|middle),double; scroll: x,y,ticks; drag: x1,y1,x2,y2
  media: key(play|next|prev|stop)  dictation: {}
  pause: seconds
  volume: mode(up|down|mute|set|get), times, level
  lock/sleep/hibernate/shutdown/restart/logoff/monitor_off: {}
  minimize_all/alt_tab/task_view: {}
  window: mode(focus|minimize|maximize|restore|close), match   wait_window: match,timeout
  search: query, engine(google|yandex|youtube)
  folder: path   writefile: path,text,mode(append|overwrite)
  clipboard_set: text   clipboard_speak: {}   clipboard_type: {}
  speak: text    play: path,wait    toast: title,text    screenshot: path
  telegram: text  ask: prompt,speak   agent: task
  if_window: match, steps[], else_steps[]   chain: ids[]"""


def ai_create_command(description: str, folder: str = "") -> Command:
    """Описание словами -> команда (использует модель агента)."""
    sys.path.insert(0, ROOT)
    from jarvis_agent.backends import BackendError, build_backend
    from jarvis_agent.config import load_config
    try:
        cfg = load_config(ROOT)
        backend = build_backend(cfg, ROOT)
    except BackendError as e:
        raise CommandError(f"нет модели для ИИ-создания: {e}") from e
    if getattr(backend, "name", "") == "echo":
        raise CommandError("нет ключа модели — ИИ-создание недоступно")
    messages = [
        {"role": "system", "content":
            "Ты конструктор голосовых команд JARVIS (как редактор Luxify). "
            "По описанию пользователя верни ТОЛЬКО JSON объекта команды, "
            "без markdown. Фразы — по-русски, короткие. Формат:\n"
            + AI_SCHEMA_HINT},
        {"role": "user", "content": str(description)},
    ]
    msg = backend.complete(messages)
    raw = str(msg.get("content") or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CommandError(f"модель вернула не JSON: {raw[:200]}") from e
    cmd = Command.from_dict(data)
    if not cmd.steps:
        raise CommandError("в сгенерированной команде нет шагов")
    cmd.folder = folder or cmd.folder
    cmd.modified = True
    lib = Library()
    lib.add(cmd)
    lib.save()
    return cmd


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #

def _print_lib(lib: Library, folder: str = "") -> None:
    folders = {f.id: f.name for f in lib.folders}
    rows = lib.commands
    if folder:
        fid = (lib.folder_by_name(folder) or Folder("", "")).id
        rows = [c for c in rows if c.folder == fid]
    if not rows:
        print("(команд нет — поставьте пак: jarvis_commands.py pack install windows)")
        return
    by_folder: dict[str, list[Command]] = {}
    for c in rows:
        by_folder.setdefault(folders.get(c.folder, "(без папки)"), []).append(c)
    for name in sorted(by_folder):
        print(f"\n[{name}]")
        for c in sorted(by_folder[name], key=lambda x: x.name.lower()):
            mark = "+" if c.enabled else "-"
            confirm = " !" if (c.confirm or any(_step_is_risky(s)
                                                for s in c.steps)) else ""
            print(f"  {mark} {c.name}{confirm}  — {', '.join(c.phrases[:2])}")
    total = len(lib.commands)
    on = len(lib.enabled_commands())
    print(f"\nВсего {total}, включено {on}. ('-' выключена, '!' подтверждение)")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="JARVIS: командный центр")
    sub = ap.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="список команд")
    p_list.add_argument("--folder", default="")

    p_match = sub.add_parser("match", help="какая команда сработает на фразу")
    p_match.add_argument("phrase")

    p_run = sub.add_parser("run", help="выполнить фразу")
    p_run.add_argument("phrase")
    p_run.add_argument("--yes", action="store_true",
                       help="подтверждать все запросы автоматически")
    p_run.add_argument("--silent", action="store_true",
                       help="не озвучивать голосом")

    p_rid = sub.add_parser("run-id", help="выполнить команду по id")
    p_rid.add_argument("cid")
    p_rid.add_argument("--yes", action="store_true")

    sub.add_parser("packs", help="список паков")
    for verb in ("install", "remove", "update"):
        pp = sub.add_parser(verb, help=f"{verb} пак команд")
        pp.add_argument("pack")

    for verb, h in (("enable", "включить команду"), ("disable", "выключить"),
                    ("rm", "удалить")):
        pp = sub.add_parser(verb, help=h)
        pp.add_argument("cid")

    p_exp = sub.add_parser("export", help="экспорт в файл")
    p_exp.add_argument("path")
    p_exp.add_argument("--what", default="")

    p_imp = sub.add_parser("import", help="импорт из файла")
    p_imp.add_argument("path")
    p_imp.add_argument("--folder", default="")

    p_ai = sub.add_parser("ai", help="создать команду с помощью ИИ")
    p_ai.add_argument("description")
    p_ai.add_argument("--folder", default="")

    p_say = sub.add_parser("say", help="озвучить текст")
    p_say.add_argument("text")

    sub.add_parser("history", help="журнал запусков")
    sub.add_parser("selftest", help="самопроверка")

    a = ap.parse_args(argv)
    action = a.cmd or "list"

    if action == "selftest":
        return selftest()

    if action == "say":
        from jarvis_voice import say
        print("ok" if say(a.text) else "не вышло")
        return 0

    if action == "packs":
        import jarvis_packs
        installed = _installed_packs()
        for pid, pack in jarvis_packs.PACKS.items():
            mark = "[x]" if pid in installed else "[ ]"
            print(f"{mark} {pid:12s} {pack['name']} — "
                  f"{len(pack['commands'])} команд. {pack.get('note', '')}")
        print("\nУстановить: python jarvis_commands.py install <id>")
        return 0

    if action in ("install", "remove", "update"):
        import jarvis_packs
        lib = Library()
        if action in ("install", "update"):
            res = install_pack(lib, a.pack)
            print(f"Пак «{a.pack}»: добавлено {res['added']}, "
                  f"обновлено {res['updated']}, пропущено (изменённые) "
                  f"{res['skipped']}.")
            return 0
        res = remove_pack(lib, a.pack)
        print(f"Пак «{a.pack}»: удалено команд {res}.")
        return 0

    lib = Library()
    if action == "list":
        _print_lib(lib, a.folder)
        return 0
    if action == "match":
        ms = match_phrase(a.phrase, lib)
        if not ms:
            parts = split_chain(a.phrase)
            print("Не понял фразу." + (f" Попробуйте по частям: {parts}"
                                       if len(parts) > 1 else ""))
            return 1
        for m in ms:
            print(f"{m.score:.2f}  {m.command.name}  "
                  f"(фраза «{m.phrase}»){'  args=' + str(m.args) if m.args else ''}")
        return 0
    if action == "run":
        ctx = ExecContext(speak=not a.silent,
                          confirm_fn=(lambda q: True) if a.yes else None)
        t0 = time.time()
        res = run_phrase(a.phrase, ctx, lib)
        print(res.text())
        print(f"({time.time() - t0:.1f} сек)")
        return 0 if res.ok else 1
    if action == "run-id":
        c = lib.command_by_id(a.cid) or lib.find_by_name(a.cid)
        if not c:
            print(f"Команда {a.cid} не найдена.")
            return 1
        ctx = ExecContext(confirm_fn=(lambda q: True) if a.yes else None)
        try:
            infos = execute_command(c, ctx)
            print("\n".join(infos) or "OK")
            return 0
        except CommandError as e:
            print(f"Ошибка: {e}")
            return 1
    if action in ("enable", "disable", "rm"):
        c = lib.command_by_id(a.cid) or lib.find_by_name(a.cid)
        if not c:
            print("Команда не найдена.")
            return 1
        if action == "rm":
            lib.remove(c.id)
            msg = f"Удалена «{c.name}»."
        else:
            c.enabled = action == "enable"
            c.modified = True
            msg = f"«{c.name}»: {'включена' if c.enabled else 'выключена'}."
        lib.save()
        print(msg)
        return 0
    if action == "export":
        payload = lib.export_payload(a.what)
        with open(a.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"Экспортировано в {a.path}: "
              f"{len(payload.get('commands', []))} команд.")
        return 0
    if action == "import":
        with open(a.path, encoding="utf-8") as f:
            payload = json.load(f)
        fid = ""
        if a.folder:
            fid = lib.ensure_folder(a.folder).id
        res = lib.import_payload(payload, fid)
        print(f"Импорт: +{res['commands']} команд, +{res['folders']} папок.")
        return 0
    if action == "ai":
        try:
            c = ai_create_command(a.description, a.folder)
        except CommandError as e:
            print(f"Не вышло: {e}")
            return 1
        print(f"Создана команда «{c.name}» ({c.id}):")
        print(json.dumps(c.to_dict(), ensure_ascii=False, indent=1))
        return 0
    if action == "history":
        for row in history(20):
            mark = "OK " if row.get("ok") else "FAIL"
            print(f"{row['t']} {mark} «{row['phrase']}» -> "
                  f"{row.get('command') or row.get('error', '')}")
        return 0
    ap.print_help()
    return 0


# --------------------------------------------------------------------------- #
#  Паки команд
# --------------------------------------------------------------------------- #

def _installed_packs() -> set[str]:
    lib = Library()
    return {c.origin.get("pack") for c in lib.commands if c.origin.get("pack")}


def install_pack(lib: Library, pack_id: str) -> dict:
    import jarvis_packs
    pack = jarvis_packs.PACKS.get(pack_id)
    if not pack:
        raise CommandError(f"нет пака «{pack_id}». Список: jarvis_commands.py packs")
    folder = lib.ensure_folder(pack["name"],
                               origin={"pack": pack_id, "kind": "folder"})
    existing = {(c.origin.get("pack"), c.origin.get("key")): c
                for c in lib.commands if c.origin.get("pack")}
    added = updated = skipped = 0
    for item in pack["commands"]:
        key = item["key"]
        prev = existing.get((pack_id, key))
        data = dict(item)
        data.pop("key", None)
        cmd = Command.from_dict(data)
        cmd.folder = folder.id
        cmd.origin = {"pack": pack_id, "key": key,
                      "version": pack.get("version", 1)}
        if prev is None:
            lib.add(cmd)
            added += 1
        elif prev.modified:
            skipped += 1
        else:
            # обновляем контент, сохраняя id
            cmd.id = prev.id
            idx = lib.commands.index(prev)
            lib.commands[idx] = cmd
            updated += 1
    lib.save()
    return {"added": added, "updated": updated, "skipped": skipped}


def remove_pack(lib: Library, pack_id: str) -> int:
    before = len(lib.commands)
    lib.commands = [c for c in lib.commands
                    if c.origin.get("pack") != pack_id]
    lib.folders = [f for f in lib.folders
                   if f.origin.get("pack") != pack_id]
    lib.save()
    return before - len(lib.commands)


# --------------------------------------------------------------------------- #
#  Самопроверка
# --------------------------------------------------------------------------- #

def selftest() -> int:
    """Автономная проверка: матчинг, библиотека, паки — без реальных действий."""
    import tempfile
    ok = fail = 0

    def check(cond: bool, what: str) -> None:
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  [ok] {what}")
        else:
            fail += 1
            print(f"  [FAIL] {what}")

    print("1. Нормализация и матчинг")
    check(normalize("Сделай ТИШЕ!!!") == "сделай тише", "нормализация текста")
    check(normalize("включи ютюб") == "включи ютуб", "замена «ютюб»→«ютуб»")

    with tempfile.TemporaryDirectory() as tmp:
        lib_path = os.path.join(tmp, "library.json")
        lib = Library(lib_path)
        c1 = lib.add(Command(id="", name="Тест тише",
                             phrases=["тише", "сделай тише"],
                             steps=[{"type": "volume", "mode": "down"}]))
        c2 = lib.add(Command(id="", name="Тест ютуб",
                             phrases=["открой ютуб"],
                             steps=[{"type": "url", "url": "https://youtube.com"}]))
        _ = lib.add(Command(id="", name="Поиск",
                             phrases=["загугли {query}"],
                             steps=[{"type": "search", "query": "{query}"}]))
        lib.save()

        ms = match_phrase("сделай тише", lib)
        check(bool(ms) and ms[0].command.id == c1.id, "точная фраза")
        ms = match_phrase("джарвис сделай тише пожалуйста", lib)
        check(bool(ms) and ms[0].command.id == c1.id, "фраза внутри текста")
        ms = match_phrase("сделай потише", lib)
        check(bool(ms) and ms[0].command.id == c1.id, "нечёткое совпадение")
        ms = match_phrase("открой ютуб и сделай тише", lib)
        check(len(ms) == 2, "цепочка из двух команд")
        ms = match_phrase("загугли котиков в космосе", lib)
        check(bool(ms) and ms[0].args.get("query") == "котиков в космосе",
              "параметр {query}")
        ms = match_phrase("абракадабра непонятная", lib)
        check(not ms, "мусор не матчится")

        print("2. Библиотека и импорт/экспорт")
        payload = lib.export_payload(c2.id)
        check(len(payload["commands"]) == 1, "экспорт одной команды")
        lib2 = Library(os.path.join(tmp, "lib2.json"))
        res = lib2.import_payload(payload)
        check(res["commands"] == 1, "импорт в другую библиотеку")
        clone = lib.clone(c1.id)
        check(clone.id != c1.id and "копия" in clone.name, "клонирование")
        lib.remove(c1.id)
        check(lib.command_by_id(c1.id) is None, "удаление")
        lib2.load()
        check(len(lib2.commands) == 1, "перезагрузка с диска")

        print("3. Dry-run выполнения")
        ctx = ExecContext(dry_run=True)
        lib3 = Library(os.path.join(tmp, "lib3.json"))
        _ = lib3.add(Command(id="", name="Сценарий", phrases=["сценарий"],
                             steps=[{"type": "launch", "target": "notepad"},
                                    {"type": "volume", "mode": "set", "level": 30},
                                    {"type": "speak", "text": "готово"}]))
        res = run_phrase("сценарий", ctx, lib3)
        check(res.ok and len(res.log) == 3, "сценарий из 3 шагов (dry-run)")

    print("4. Паки")
    import jarvis_packs
    check(len(jarvis_packs.PACKS) >= 5, f"паков: {len(jarvis_packs.PACKS)}")
    bad = []
    for pid, pack in jarvis_packs.PACKS.items():
        seen = set()
        for item in pack["commands"]:
            if "key" not in item or not item.get("phrases") \
                    or not item.get("steps"):
                bad.append(f"{pid}:{item.get('key')}")
            if item["key"] in seen:
                bad.append(f"{pid}:{item['key']} (дубль)")
            seen.add(item["key"])
            for s in item.get("steps", []):
                if not isinstance(s, dict) or not s.get("type"):
                    bad.append(f"{pid}:{item['key']} шаг без type")
    check(not bad, "структура паков в порядке" +
          (f" — проблемы: {bad[:5]}" if bad else ""))

    print(f"\nИтог: {ok} ок, {fail} проблем.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
