"""Turn JARVIS's Telegram new-message watcher on or off, or report its state.

With the watcher ON, JARVIS checks the owner's personal Telegram account for new
incoming messages about once a minute and says them aloud (in Russian) whenever
it is awake and not mid-conversation. This action only flips the switch and reads
it back; the actual watching lives in main.py's `_run_telegram_watch`.

It sends nothing and never touches the Telegram remote-control bot — those are
different things: the bot is an inbound command channel, this is an outbound
"tell me what arrived" reader.
"""

from __future__ import annotations

from memory.config_manager import (
    get_telegram_watch_enabled,
    save_telegram_watch_enabled,
)


def telegram_watch(parameters=None, **_ctx) -> str:
    action = str((parameters or {}).get("action") or "status").strip().lower()

    if action in {"on", "start", "enable", "вкл", "включить"}:
        save_telegram_watch_enabled(True)
        return (
            "Включаю слежение за Telegram: новые сообщения буду сообщать вслух. "
            "Проверяю примерно раз в минуту."
        )
    if action in {"off", "stop", "disable", "выкл", "выключить"}:
        save_telegram_watch_enabled(False)
        return "Выключаю слежение за Telegram. Больше не буду сообщать о новых сообщениях."

    enabled = get_telegram_watch_enabled()
    return (
        "Слежение за новыми сообщениями Telegram сейчас включено."
        if enabled
        else "Слежение за новыми сообщениями Telegram сейчас выключено."
    )


TOOL = {
    "name": "telegram_watch",
    "description": (
        "Turn JARVIS's Telegram new-message watcher on or off, or report its state. "
        "When ON, JARVIS reads the owner's personal Telegram account about once a "
        "minute and SAYS new incoming messages aloud in Russian. Use it when the "
        "owner asks JARVIS to follow / watch / report Telegram messages — 'следи за "
        "сообщениями в телеграм', 'пусть говорит новые сообщения из тг', 'сообщай о "
        "новых сообщениях в телеграме' — or to stop — 'перестань следить за "
        "телеграм', 'не сообщай больше о сообщениях', 'выключи слежение за тг'. "
        "This does NOT send any Telegram message and does NOT control the Telegram "
        "remote-control bot; it only toggles the spoken new-message monitoring."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["on", "off", "status"],
                "description": (
                    "on = start watching and speaking new Telegram messages; "
                    "off = stop; status = report whether it is currently on."
                ),
            }
        },
        "required": ["action"],
    },
    "handler": telegram_watch,
}
