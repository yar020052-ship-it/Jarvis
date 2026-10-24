# Телеметрия ошибок JARVIS

## Архитектура

```
Чужой JARVIS (у пользователя) → ошибка → traceback → Telegram Bot API → твой чат
                                                                          ↓
                                                              Ты нажимаешь кнопку в боте
                                                                          ↓
                                                              Поллер показывает ошибки
```

## Файлы

- `core/telemetry.py` — перехват необработанных исключений, отправка в Telegram
- `scripts/error_bot_poller.py` — бот-поллер для просмотра ошибок по кнопке

## Настройка разработчика

### 1. Запусти поллер на своём ПК

```powershell
python scripts/error_bot_poller.py
```

Поллер работает в фоне и обрабатывает нажатия кнопки «Ошибки пользователей» в боте.

### 2. Структура данных

Ошибки хранятся в `%USERPROFILE%\Documents\JARVIS\received_errors\errors.json`

### 3. Команды бота

- `/start` — приветствие + кнопки
- `/errors` — последние ошибки
- `/clear` — очистить список
- `/add_test` — добавить тестовую ошибку

## Сборка дистрибутива для отправки другим людям

1. Собери релиз:
   ```powershell
   python scripts/release.py --version 0.1.2
   ```

2. Собери установочный бандл:
   ```powershell
   python scripts/make_dist_bundle.py --version 0.1.2
   ```

3. Отправь `dist/Jarvis-Install-Windows-v0.1.2-Telemetry.zip` через Telegram

## Что отправляется

- ID копии (анонимизированный)
- Версия JARVIS
- ОС
- Короткий traceback (до 1500 символов)

## Что НЕ отправляется

- API-ключи, токены
- Личные данные, история диалогов
- IP-адрес, геолокация
