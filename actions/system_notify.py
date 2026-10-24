"""Show an immediate, local notification without scheduling a task."""

from __future__ import annotations

import platform


def _clean(value: object, limit: int) -> str:
    text = str(value or "").replace("\x00", " ")
    return " ".join(text.split())[:limit].strip()


def system_notify(parameters: dict | None = None, **_ctx) -> str:
    p = parameters or {}
    title = _clean(p.get("title") or "J.A.R.V.I.S", 80)
    body = _clean(p.get("body"), 500)
    subtitle = _clean(p.get("subtitle"), 120)
    sound = p.get("sound", True) is not False

    if not body:
        raise ValueError("Укажи текст уведомления в параметре body.")

    message = f"{subtitle}\n{body}" if subtitle else body

    if platform.system() == "Windows":
        try:
            from winotify import Notification, audio

            toast = Notification(
                app_id="JARVIS",
                title=title,
                msg=message,
                duration="short",
            )
            toast.set_audio(audio.Default if sound else audio.Silent, loop=False)
            toast.show()
            return "Уведомление JARVIS показано на этом ПК."
        except Exception as exc:
            # Use the existing cross-platform notifier only if WinToast is
            # unavailable; it cannot honor the optional silent setting.
            if not sound:
                raise RuntimeError(
                    "Windows не смог показать бесшумное уведомление через WinToast."
                ) from exc

    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=10)
    except Exception as exc:
        raise RuntimeError("Не удалось показать системное уведомление.") from exc

    return "Системное уведомление отправлено."


TOOL = {
    "name": "system_notify",
    "description": (
        "Immediately displays a local desktop notification. Use when a short "
        "message should appear on this PC now; use reminder for a future time. "
        "Does not send the message to another device."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "title": {
                "type": "STRING",
                "description": "Notification title; defaults to J.A.R.V.I.S.",
            },
            "body": {
                "type": "STRING",
                "description": "Notification message text.",
            },
            "subtitle": {
                "type": "STRING",
                "description": "Optional short line shown before the message.",
            },
            "sound": {
                "type": "BOOLEAN",
                "description": "Play the Windows notification sound; defaults to true.",
            },
        },
        "required": ["body"],
    },
    "handler": system_notify,
}
