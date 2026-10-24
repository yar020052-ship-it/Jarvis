# JARVIS — универсальный установщик (setup-bootstrapper)

Цель: чтобы другой человек мог поставить JARVIS на свой ПК **со своими данными**, установщик сам определял что нужно и качал под этот компьютер. Обновления показываются **внутри** JARVIS.

## 1) Структура
- Приложение (неизменяемое при обновлении): папка установки (`%LOCALAPPDATA%\Programs\Jarvis\` на Windows, `~/.local/share/Jarvis/` на Linux, `~/Library/Application Support/Jarvis/` на macOS)
- Пользовательские данные (конфиги/ключи/БД/логи/кэш/модели): `%LOCALAPPDATA%\JARVIS\` (Windows) или эквивалент ОС. **Никогда** не трогаются при обновлении.

## 2) Bootstrapper
Windows (PowerShell):
```powershell
irm https://raw.githubusercontent.com/yar020052-ship-it/Jarvis/main/scripts/bootstrap/setup-jarvis.ps1 | iex
```

Linux/macOS (bash):
```bash
curl -sL https://raw.githubusercontent.com/yar020052-ship-it/Jarvis/main/scripts/bootstrap/setup-jarvis.sh | bash
```

Bootstrapper:
- определяет ОС, архитектуру, платформу (`windows-x64/linux-x64/darwin-arm64/...`)
- скачивает `latest.json` с GitHub Releases
- скачивает правильный zip под платформу
- при первом запуске устанавливает в нужную папку; при повторном — запускает `scripts/update.py` поверх (исключая user-data)

## 3) Релизы (GitHub Releases)
В `dist/` после `scripts/release.py` появятся:
- `Jarvis-vX.Y.Z-win-x64.zip`, `-linux-x64.zip`, `-darwin-x64.zip`, `-darwin-arm64.zip`, `-win-arm64.zip`
- `SHA256SUMS.txt`
- `latest.json`

Структура `latest.json`:
```json
{
  "version": "0.1.1",
  "channel": "stable",
  "min_supported": "0.1.0",
  "platforms": {
    "windows-x64": {"url": "...", "sha256": "...", "size_mb": 123.4}
  },
  "changelog_ru": "..."
}
```

## 4) Как сделать релиз
1. Обновить версию в `__version__.py` и `jarvis/__version__.py` (и при желании в `pyproject.toml`)
2. `python scripts/release.py --version 0.1.1`
3. Залить файлы из `dist/` в GitHub Release `v0.1.1`
4. У всех клиентов при следующем старте внутри Jarvis появится уведомление «Доступно обновление»

## 5) Безопасность
- Апдейтер копирует **только** файлы приложения, жёстко исключает: `config`, `logs`, `memory`, `cache`, `models`, `.venv`, `__pycache__`
- Проверка `SHA256` при скачивании
- `min_supported` защищает от несовместимых версий
- Никаких телеметрий/пушей — только HTTP-запрос `latest.json`

## 6) Первичный запуск
При первом старте Jarvis использует user-data в `%LOCALAPPDATA%\JARVIS\` (Windows). Конфиг `api_keys.json` и прочие пользовательские файлы живут там. Если в папке приложения остались старые `config/` — код автоматически мигрирует их в user-data.

## 7) Настройка под свой репозиторий
В файлах заменить `yar020052-ship-it` на `username/repo`:
- `scripts/bootstrap/setup-jarvis.ps1`
- `scripts/bootstrap/setup-jarvis.sh`
- `scripts/release.py`
- `jarvis/core/update_integration.py`
- `actions/update_check.py`

## 8) Быстрый тест
В текущем окружении: `python -c "from core.updater import APP_VERSION,_detect_platform; print(APP_VERSION,_detect_platform())"` — должно вывести `0.1.0 windows-x64`
