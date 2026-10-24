import json
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

# Токен не хардкодится: берём из окружения или из конфига Telegram-плагина
# (тот же источник, что и у core/telemetry.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.telemetry import _telegram_config  # noqa: E402

BOT_TOKEN, CHAT_ID = _telegram_config()
if not BOT_TOKEN or not CHAT_ID:
    sys.exit("Токен/чат не настроены: задай JARVIS_TELEMETRY_TOKEN и JARVIS_TELEMETRY_CHAT_ID.")

try:
    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
except Exception:
    ctx = ssl._create_unverified_context()

def api(method, payload):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        return json.loads(resp.read().decode())

# Отправляем сообщение с кнопкой "Ошибки пользователей"
keyboard = {
    "inline_keyboard": [
        [{"text": "Ошибки пользователей", "callback_data": "show_errors"}],
        [{"text": "Обновить", "callback_data": "refresh_errors"}],
    ]
}

resp = api("sendMessage", {
    "chat_id": CHAT_ID,
    "text": "JARVIS Error Bot — проверка. Нажми кнопку ниже.",
    "reply_markup": keyboard,
})

msg_id = resp["result"]["message_id"]
print(f"Отправлено сообщение: message_id={msg_id}")
print(f"Текст: {resp['result']['text']}")
print(f"Кнопки: {len(resp['result']['reply_markup']['inline_keyboard'])}")
print()
print("Теперь нажми кнопку 'Ошибки пользователей' в Telegram...")
print("Ждём 20 секунд, потом проверяем, изменилось ли сообщение.")
print()

time.sleep(20)

# Проверяем, изменилось ли сообщение (после нажатия поллер должен его отредактировать)
try:
    # Получаем updates с offset=0, чтобы увидеть callback
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?timeout=1",
    )
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    
    callbacks = [u for u in data.get("result", []) if "callback_query" in u]
    print(f"Найдено callback_query: {len(callbacks)}")
    
    if callbacks:
        for cb in callbacks:
            print(f"  - data={cb['callback_query']['data']}, id={cb['callback_query']['id']}")
        print()
        print("Кнопка работает! Поллер получил нажатие.")
    else:
        print("Callback не найден. Возможно, поллер не запущен или кнопка не нажата.")
        print()
        print("Проверь:")
        print("1. Запущен ли поллер: python scripts\\error_bot_poller.py")
        print("2. Нажал ли ты кнопку в Telegram")
        print()
        print("Подсказка: если поллер запущен, он должен вывести:")
        print("  '[ErrorBot] Poller запущен...'")
        print("  И при нажатии — отредактировать сообщение в чате.")
except Exception as e:
    print(f"Ошибка проверки: {e}")
