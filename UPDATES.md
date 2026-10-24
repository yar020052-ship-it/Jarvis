# Система обновлений JARVIS

## Как это работает
1. При старте Jarvis в фоне проверяет `latest.json` на GitHub Releases (по URL из `jarvis/core/update_integration.py`/`actions/update_check.py`)
2. Если есть версия > текущей (`__version__.py`) — показывается **внутреннее уведомление** в UI Jarvis
3. Кнопки: «Обновить», «Позже», «Что нового»
4. «Обновить» — скачивает zip под платформу в кеш, проверяет SHA256, запускает `scripts/update.py` и закрывает Jarvis. Апдейтер распаковывает поверх app, **не трогая** user-data (`%LOCALAPPDATA%\JARVIS\` и др.)
5. После обновления Jarvis перезапускается

## Файлы
- `__version__.py`, `jarvis/__version__.py` — текущая версия
- `core/paths.py` — разделение app/user-data, миграция старых файлов
- `jarvis/core/updater.py` — проверка манифеста, сравнение версий, скачивание
- `scripts/update.py` — безопасный апдейтер (exclude user-data)
- `scripts/release.py` — генерация релизного пакета (zip + SHA256 + latest.json)
- `scripts/bootstrap/*` — bootstrapper для установки на чужом ПК
- `actions/update_check.py` — ручная проверка из Jarvis

## Релиз
```bash
python scripts/release.py --version 0.1.1
```
Затем залить `dist/*` в GitHub Release `v0.1.1`.

## Репозиторий обновлений
Настроен: `yar020052-ship-it/Jarvis`. Манифест: `https://github.com/yar020052-ship-it/Jarvis/releases/latest/download/latest.json`.
Адрес зашит в:
- `scripts/bootstrap/setup-jarvis.ps1`
- `scripts/bootstrap/setup-jarvis.sh`
- `scripts/release.py`
- `jarvis/core/update_integration.py`
- `jarvis/core/update_ui.py`
