# ROADMAP

План дальнейшего развития JARVIS. Датирован: 2026-10-26.

## Готово (2026-10-08 – 2026-10-26)

- [x] Полный аудит проекта.
- [x] Learning loop: tool failure + owner correction storage (`main.py`, `memory/lessons.py`).
- [x] Sticky-scroll в `ChatWidget` (`ui.py`).
- [x] Background task system (`background.py`, `actions/background_tasks.py`).
- [x] Windows 11 automation helpers (`windows_automation.py`, `actions/computer_settings.py`).
- [x] GitHub Scout: кандидаты, реестр, принятые фрагменты, интеграция безопасных задач.
- [x] Тестовая база: pytest config, 203 теста, документация по тестированию.

## Ближайшие этапы

1. **Финальное ревью и коммит** — разбить изменения на логические батчи коммитов.
2. **Визуальная верификация** — sticky-scroll и chat UI на реальном display.
3. **Интеграция Windows helpers в voice action** — только если понадобится новая операция, не дублируя существующую.
4. **Расширение background whitelist** — только безопасные задачи.
5. **Медиа/мозг**: Telegram media improvements, voice reply pipeline — продолжить после стабилизации.

## Не планируемо

- Удаление пользовательских изменений без явного подтверждения.
- Внедрение кода с копилефтной лицензией (AGPL/GPL) без юридической проверки.
