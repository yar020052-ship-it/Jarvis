"""Телеметрия ошибок JARVIS — отправка traceback'ов разработчику через Telegram.

Модуль включён по умолчанию (опция "молча включено"). При необработанном
исключении формируется краткое сообщение и отправляется в чат Telegram.

Содержимое сообщения:
- Имя пользователя/копии (machine_id, не username)
- Версия JARVIS
- Короткий traceback (до 1500 символов)

Не отправляются:
- API-ключи, токены, пароли
- Личные файлы, история диалогов
- IP-адрес, геолокация
"""
from __future__ import annotations

import json
import os
import platform
import traceback
import urllib.request
import urllib.error
from pathlib import Path

# ── Конфигурация ─────────────────────────────────────────────────────────────
#
# Токен и chat_id НИКОГДА не хардкодятся в исходниках: раньше они лежали здесь
# строкой, и каждый, кто получил копию кода, умел писать от имени бота и читать
# чужую телеметрию. Теперь — переменные окружения либо тот же конфиг, куда их
# пишет владелец через настройки Telegram-плагина.

_MAX_TB_LEN = 1500
_SEND_TIMEOUT = 10  # секунд


def _telegram_config() -> tuple[str, str]:
    """(bot_token, chat_id) из окружения или config/api_keys.json. Пустые
    строки, если владелец телеметрию не настраивал — тогда отправки нет."""
    token = os.environ.get("JARVIS_TELEMETRY_TOKEN", "")
    chat_id = os.environ.get("JARVIS_TELEMETRY_CHAT_ID", "")
    if token and chat_id:
        return token, chat_id
    try:
        cfg = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        tg = (data.get("plugin_config") or {}).get("telegram_remote") or {}
        token = token or str(tg.get("bot_token") or "")
        chat_id = chat_id or str(tg.get("allowed_chat_ids") or "").split(",")[0].strip()
    except Exception:
        pass
    return token, chat_id


def _machine_id() -> str:
    """Краткий идентификатор копии (не username, не hostname)."""
    try:
        import uuid

        node = uuid.getnode()  # MAC-адрес, но как число
        return f"ID-{node:012X}"[-12:]
    except Exception:
        return "UNKNOWN"


def _version() -> str:
    try:
        from __version__ import __version__

        return __version__
    except Exception:
        return "?"


def _format_message(exc_type, exc_value, tb) -> str:
    """Краткое сообщение об ошибке для Telegram."""
    lines = [
        f"**JARVIS {_version()} — ошибка**",
        f"Копия: `{_machine_id()}`",
        f"ОС: {platform.system()} {platform.release()}",
        "",
    ]
    # Последние 10 строк traceback
    try:
        tb_lines = traceback.format_exception(exc_type, exc_value, tb)
        tail = "".join(tb_lines[-10:])[:_MAX_TB_LEN]
        lines.append("```")
        lines.append(tail)
        lines.append("```")
    except Exception:
        lines.append(f"{exc_type.__name__}: {exc_value}")
    return "\n".join(lines)


def send_error(message: str) -> bool:
    """Отправляет сообщение в Telegram. Тихо возвращает False при любой ошибке."""
    token, chat_id = _telegram_config()
    if not token or not chat_id:
        return False
    try:
        import ssl
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=_SEND_TIMEOUT, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return '"ok":true' in body
    except Exception:
        return False


def send_test_message() -> bool:
    """Тестовое сообщение для проверки конфигурации."""
    msg = (
        f"**JARVIS {_version()} — тест телеметрии**\n"
        f"Копия: `{_machine_id()}`\n"
        f"ОС: {platform.system()} {platform.release()}\n"
        "Отправка ошибок работает."
    )
    return send_error(msg)


def install_excepthook() -> None:
    """Перехватывает необработанные исключения и шлёт их в Telegram."""
    _orig = None

    def _hook(exc_type, exc_value, tb):
        try:
            msg = _format_message(exc_type, exc_value, tb)
            send_error(msg)
        except Exception:
            pass
        if _orig:
            _orig(exc_type, exc_value, tb)

    import sys

    _orig = sys.excepthook
    sys.excepthook = _hook


__all__ = ["send_error", "send_test_message", "install_excepthook"]
