"""Сторож: следит за логом ассистента, замечает падения, чинит.

Запускается из main.py одним вызовом start() — дальше живёт своим тихим
потоком-демоном и никогда не роняет основной процесс (все ошибки глушатся).

Что делает по кругу (раз в минуту):
  1. Дочитывает logs/console.log с прошлой позиции.
  2. Ищет свежие Traceback/RuntimeError в коде самого JARVIS.
  3. Регистрирует инцидент и, если включён auto_repair, чинит (снапшот →
     патч от Gemini → проверка → откат). Лимиты: 3 попытки на инцидент,
     10 починок в сутки.
  4. Пишет итог в дневник простым языком.
"""
from __future__ import annotations

import re
import threading
import time

from . import BASE_DIR, STATE_DIR

LOG_FILE = BASE_DIR / "logs" / "console.log"
POS_FILE = STATE_DIR / "watchdog.pos"
REPAIRS_FILE = STATE_DIR / "repairs_today.json"

INTERVAL = 60.0
MAX_PER_DAY = 10

_TB_START = re.compile(r"^Traceback \(most recent call last\):", re.M)
_ERROR_LINE = re.compile(
    r"^(?:.*?(?:Error|Exception|FAILED|Traceback)[^\n]{0,200})$", re.M)

# Ожидаемые ошибки: это не поломка кода, а состояние среды или выбор человека.
# Их бессмысленно «чинить» патчем, поэтому инцидент не заводим и Gemini не
# тратим — только тихая заметка в дневник. Маркеры сравниваются с последней
# строкой traceback (RuntimeError: ...), а не со всем блоком: в исходных
# строках кадров может случайно встретиться любое слово.
_EXPECTED_REASONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "нет ключа API",
        (
            "api key is not configured",
            "api key not configured",
            "no api key",
            "missing api key",
            "api key is missing",
            "api key not valid",
            "нет ключа",
            "ключ api не настроен",
        ),
    ),
    (
        "несколько устройств",
        (
            "multiple devices",
            "more than one device",
            "several devices",
            "несколько устройств",
            "выберите device_id",
            "укажите device_id",
            "укажите serial",
        ),
    ),
    (
        "ADB недоступен",
        (
            "adb не найден",
            "adb not found",
            "adb is not installed",
            "adb не запущен",
            "adb server",
            "device offline",
            "device unauthorized",
            "no android device",
            "no devices",
            "нет android-устройства",
            "не найдено в режиме",
            "android-устройство не подключено",
        ),
    ),
    (
        "пользователь отказал",
        (
            "user denied",
            "denied by user",
            "denied by owner",
            "owner denied",
            "user cancelled the operation",
            "cancelled by user",
            "пользователь отказ",
            "отменено пользователем",
            "не подтверждено",
        ),
    ),
    (
        "зависимости дашборда",
        (
            "fastapi/uvicorn not installed",
            "uvicorn not installed",
            "no module named 'fastapi'",
            "no module named \"fastapi\"",
            "no module named 'uvicorn'",
            "no module named \"uvicorn\"",
            "no module named 'cryptography'",
            "no module named \"cryptography\"",
            "dashboard disabled",
        ),
    ),
)


def expected_failure_reason(text: str) -> str | None:
    """Вернуть причину, если ошибка ожидаемая и чинить её не нужно."""
    low = str(text or "").lower().replace("ё", "е")
    for reason, markers in _EXPECTED_REASONS:
        if any(marker in low for marker in markers):
            return reason
    return None

_thread: threading.Thread | None = None
_stop = threading.Event()


def _settings() -> dict:
    s = {"enabled": True, "auto_repair": True}
    try:
        from memory.config_manager import load_api_keys

        data = load_api_keys()
        s["enabled"] = bool(data.get("autonomy_enabled", True))
        s["auto_repair"] = bool(data.get("autonomy_auto_repair", True))
    except Exception as exc:  # noqa: BLE001
        from core.diagnostics import log_exception

        log_exception("autonomy.watchdog._settings", exc)
    return s


def _repairs_today() -> int:
    import json
    from datetime import datetime

    day = datetime.now().strftime("%Y-%m-%d")
    try:
        data = json.loads(REPAIRS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return int(data.get(day, 0))


def _own_code_file(text: str) -> str | None:
    """Вытащить из traceback первый файл самого JARVIS (actions/plugins/...)."""
    for line in text.splitlines():
        m = re.search(r'File "([^"]+)", line \d+', line)
        if not m:
            continue
        path = m.group(1).replace("\\", "/")
        for prefix in ("actions/", "plugins/", "dashboard/", "autonomy/"):
            if prefix in path:
                return path[path.index(prefix):]
    return None


def _drain_new() -> str:
    try:
        pos = int(POS_FILE.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        pos = 0
    try:
        size = LOG_FILE.stat().st_size
    except OSError:
        return ""
    if size < pos:  # лог пересоздан (ротация VBS) — читаем с начала
        pos = 0
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            f.seek(pos)
            chunk = f.read(200000)
            new_pos = f.tell()
    except OSError:
        return ""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        POS_FILE.write_text(str(new_pos), encoding="utf-8")
    except OSError:
        pass
    return chunk


def _handle_traceback(
    block: str,
    settings: dict,
    seen: set[tuple[str, str]],
    incidents_mod,
    journal_mod,
    repair_mod,
) -> str:
    """Разобрать один traceback и вернуть решение: skipped/expected/noted/repaired.

    Ожидаемые ошибки (нет ключа, несколько устройств, ADB offline, отказ
    пользователя, отсутствующие зависимости дашборда) не заводят инцидент и не
    запускают починку — только одна заметка в дневник на каждый уникальный
    текст. Иначе автономность тратит лимит починок на то, что кодом не чинится.
    """
    err_line = block.strip().splitlines()[-1][:300]
    # Чужие ошибки (сеть, API) — не наши падения, пропускаем
    if "autonomy" in err_line and "watchdog" in err_line:
        return "skipped"
    rel = _own_code_file(block)
    if not rel:
        return "skipped"
    expected = expected_failure_reason(err_line)
    if expected:
        key = (expected, err_line[:200])
        if key not in seen:
            if len(seen) < 200:
                seen.add(key)
            journal_mod.add(
                "note",
                f"ожидаемая ситуация ({expected}) в {rel}: "
                f"{err_line[:120]} — не чиню",
            )
        return "expected"
    inc = incidents_mod.add("crash", err_line, where=rel, tb=block)
    if not settings["auto_repair"]:
        journal_mod.add("note", f"заметил падение в {rel}: {err_line[:120]} "
                                f"(автопочинка выключена)")
        return "noted"
    if _repairs_today() >= MAX_PER_DAY:
        journal_mod.add("note", "дневной лимит починок исчерпан, жду завтра")
        return "noted"
    repair_mod.repair_incident(inc)
    # Итог repair_incident уже записан в дневник; сюда — ничего лишнего
    return "repaired"


def _loop() -> None:
    from . import incidents as incidents_mod
    from . import journal as journal_mod
    from . import repair as repair_mod

    expected_seen: set[tuple[str, str]] = set()

    while not _stop.is_set():
        try:
            time.sleep(INTERVAL)
            if _stop.is_set():
                break
            settings = _settings()
            if not settings["enabled"]:
                continue
            chunk = _drain_new()
            if not chunk:
                continue
            blocks = _TB_START.split(chunk)
            for tail in blocks[1:]:
                block = "Traceback (most recent call last):" + tail[:4000]
                _handle_traceback(
                    block, settings, expected_seen,
                    incidents_mod, journal_mod, repair_mod,
                )
        except Exception:
            continue  # сторож не имеет права падать


def start() -> bool:
    """Запустить сторожа (идемпотентно). Возвращает True если запущен."""
    global _thread
    if _thread and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="jarvis-autonomy-watch",
                               daemon=True)
    _thread.start()
    try:
        from . import journal as journal_mod

        journal_mod.add("note", "сторож автономности на посту")
    except Exception:
        pass
    return True
