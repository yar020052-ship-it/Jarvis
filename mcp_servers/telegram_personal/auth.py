"""Interactive, local-only setup for the personal Telegram MCP connector."""

from __future__ import annotations

import asyncio
import getpass
import json
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)


def data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise RuntimeError("LOCALAPPDATA is not set; this connector currently targets Windows.")
    path = Path(root) / "JARVIS" / "mcp" / "telegram-personal"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _delivery_hint(sent) -> str:
    kind = type(getattr(sent, "type", None)).__name__
    hints = {
        "SentCodeTypeApp": "уведомление внутри Telegram (проверьте официальный чат Telegram на уже авторизованном устройстве)",
        "SentCodeTypeSms": "SMS на введённый номер",
        "SentCodeTypeCall": "телефонный звонок",
        "SentCodeTypeFlashCall": "входящий звонок; проверьте номер звонящего по подсказке Telegram",
        "SentCodeTypeEmailCode": "электронная почта, указанная Telegram",
        "SentCodeTypeFragmentSms": "SMS через Telegram Fragment",
    }
    hint = hints.get(kind, f"канал Telegram ({kind or 'неизвестный тип'})")
    timeout = getattr(sent, "timeout", None)
    suffix = f" Повторная отправка станет доступна через {timeout} сек." if timeout else ""
    return f"Способ доставки кода: {hint}.{suffix}"


async def _login() -> None:
    folder = data_dir()
    config_path = folder / "credentials.json"
    session_path = folder / "account"

    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    else:
        existing = {}

    api_id = existing.get("api_id")
    api_hash = existing.get("api_hash")
    if not api_id or not api_hash:
        print("Get API ID and API Hash at https://my.telegram.org/apps.")
        api_id = int(input("Telegram API ID: ").strip())
        api_hash = getpass.getpass("Telegram API Hash (hidden): ").strip()
        if not api_hash:
            raise RuntimeError("API Hash cannot be empty.")

    client = TelegramClient(str(session_path), int(api_id), api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            phone = input("Telegram phone number with country code: ").strip()
            sent = await client.send_code_request(phone)
            print(_delivery_hint(sent))
            # Persist only after Telegram accepted the API pair; this lets a
            # user restart after a mistyped/expired login code without retyping it.
            config_path.write_text(
                json.dumps({"api_id": int(api_id), "api_hash": api_hash}, indent=2) + "\n",
                encoding="utf-8",
            )
            for attempt in range(3):
                code = getpass.getpass("Telegram login code (hidden; enter Q to cancel): ").strip()
                if code.lower() == "q":
                    raise RuntimeError(
                        "Вход отменён. Telegram сам выбирает доступный канал доставки кода; "
                        "повторная отправка SMS через устаревший force_sms недоступна."
                    )
                try:
                    await client.sign_in(phone=phone, code=code)
                    break
                except PhoneCodeInvalidError:
                    if attempt == 2:
                        raise RuntimeError(
                            "Telegram отклонил код после трёх попыток. Запустите вход заново и используйте самый свежий код из служебного чата Telegram."
                        )
                    print("Telegram отклонил введённый код. Используйте только самый свежий код из Telegram.")
                except PhoneCodeExpiredError as exc:
                    raise RuntimeError(
                        "Срок действия кода истёк. Запустите вход заново, чтобы запросить новый код."
                    ) from exc
                except SessionPasswordNeededError:
                    password = getpass.getpass("Telegram 2FA password (hidden): ")
                    await client.sign_in(password=password)
                    break

        user = await client.get_me()
        config_path.write_text(
            json.dumps({"api_id": int(api_id), "api_hash": api_hash}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Connected locally as {user.first_name} (@{user.username or 'no username'}).")
        print(f"Credentials and session are stored under: {folder}")
        print("Do not share this folder or its session file; it grants account access.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(_login())
    except Exception as exc:
        print(f"Авторизация не завершена: {exc}")
