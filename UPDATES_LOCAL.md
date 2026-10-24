# Обновления без GitHub (через файл/локальный сервер)

Вместо GitHub Releases можно просто передавать zip + latest.json (через Telegram, Яндекс.Диск прямой ссылкой, локальную сеть).

## 1) Формат latest.json
```json
{
  "version": "0.1.1",
  "channel": "stable",
  "min_supported": "0.1.0",
  "changelog_ru": "- Исправления\n- Новые фишки",
  "platforms": {
    "windows-x64": { "url": "file:///C:/Updates/Jarvis-v0.1.1-win-x64.zip", "sha256": "..." },
    "linux-x64":   { "url": "https://example.com/Jarvis-v0.1.1-linux-x64.zip", "sha256": "..." }
  }
}
```

`url` может быть:
- `file://` — локальный файл
- `http://`/`https://` — прямая ссылка на zip
- относительный/абсолютный путь — тоже работает

## 2) Как включить проверку
Jarvis читает переменную окружения `JARVIS_UPDATE_MANIFEST`.

Windows (в ярлыке/батнике):
```bat
set JARVIS_UPDATE_MANIFEST=file://C:/Users/RODION/Updates/latest.json
python main.py
```

Или в `launch_hidden.vbs` добавить:
```vbs
shell.Environment("PROCESS")("JARVIS_UPDATE_MANIFEST") = "file://C:/path/latest.json"
```

## 3) Сгенерировать релиз-пакет локально
```bash
python scripts/release.py --version 0.1.1
```
Получите `dist/*.zip`, `dist/latest.json`, `dist/SHA256SUMS.txt`. 
Возьмите нужный zip + подправьте `latest.json` под выбранный способ распространения (file:// или прямая ссылка).

## 4) Проверка из Jarvis
Можно спросить у Jarvis: "проверь обновления" — сработает action `update_check`.

## 5) Автопроверка при старте
Если `JARVIS_UPDATE_MANIFEST` задан — при старте появится уведомление при наличии новой версии.
