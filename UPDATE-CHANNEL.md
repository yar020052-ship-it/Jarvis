# Канал обновлений JARVIS

Чтобы у других пользователей приходили ваши обновления, JARVIS должен знать адрес
файла `latest.json`. Этот файл лежит в интернете или в общей сетевой папке и
описывает последнюю версию.

## Вариант через GitHub Releases

1. Создайте репозиторий GitHub, например `https://github.com/yar020052-ship-it/Jarvis`.
2. Когда внесли изменения, увеличьте версию в файлах:
   - `__version__.py`
   - `jarvis/__version__.py`
3. Соберите релиз:

```powershell
.\BUILD-UPDATE-RELEASE.ps1 -Version 0.1.3 -Repo "yar020052-ship-it/Jarvis" -AutoInstall -ChangelogRu "Исправления и новые функции"
```

4. В папке `dist` появятся ZIP-файлы, `SHA256SUMS.txt` и `latest.json`.
5. Загрузите файлы из `dist` в GitHub Release с тегом `v0.1.3`.
6. Новым пользователям устанавливайте JARVIS так:

```powershell
.\setup-jarvis-local.ps1 -ManifestUrl "https://github.com/yar020052-ship-it/Jarvis/releases/latest/download/latest.json"
```

После этого установленный JARVIS будет проверять этот адрес при запуске.

## Автоматическая установка

Если хотите, чтобы обновление ставилось само при запуске JARVIS, добавьте в
`latest.json`:

```json
"auto_install": true
```

Если `auto_install` не указан или равен `false`, JARVIS только покажет уведомление
о новой версии.

## Вариант без GitHub

Можно положить `latest.json` и ZIP обновления на любой прямой HTTPS-адрес или в
общую папку. Главное, чтобы в `latest.json` поле `platforms.windows-x64.url`
указывало на ZIP обновления.

Минимальный пример:

```json
{
  "version": "0.1.3",
  "channel": "stable",
  "changelog_ru": "Исправления и новые функции",
  "auto_install": true,
  "platforms": {
    "windows-x64": {
      "url": "https://example.com/Jarvis-v0.1.3-windows-x64.zip",
      "sha256": ""
    }
  }
}
```
