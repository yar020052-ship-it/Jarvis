# Переход с OpenClaw Companion на JARVIS

Состояние на 20.10.2026, 15:03 (Europe/Moscow). Проверялся активный JARVIS в `C:\Users\RODION\Documents\Codex\2026-10-08\https-t-me-c-1885250891-304\outputs\JARVIS`.

## Итог

- OpenClaw Companion 2026.9.8-1 удалён штатным деинсталлятором. Он завершился с кодом 0; запись установленного приложения, каталог `F:\OpenClawTray`, ярлык автозапуска и процесс Companion отсутствуют.
- В активном `config/mcp_servers.json` нет `openclaw_companion_local`; остальные четыре MCP-сервера сохранены. Чистый запуск JARVIS после удаления прошёл из каталога выше.
- Новый журнал подтверждает 51 активное действие, включая `system_notify` и `system_which`, 12 плагинов, 4 MCP-сервера и подключение к Gemini Live. Панель JARVIS слушает 8000/8001; HTTPS `/login` вернул 200.
- Полная копия профиля Companion сохранена до деинсталляции: `C:\Users\RODION\AppData\Local\JARVIS\migration-backups\OpenClawTray-final-pre-uninstall-20261020-145326`. В ней 5 файлов (24 262 байта); контрольные суммы совпадают с предыдущим снимком, ACL разрешает доступ только текущей учётной записи и SYSTEM.
- Деинсталлятор оставил 3 файла в `%APPDATA%\OpenClawTray`; остальные данные профиля есть в защищённой копии. Профиль целиком не удалялся.

## Что перенесено и что заменяет Companion

| Возможность Companion | Состояние до удаления | Замена в JARVIS |
|---|---|---|
| `system.notify` | Доступно локально | Добавлено нативное действие `system_notify`; оно показало бесшумное локальное уведомление. |
| `system.which` | Доступно локально | Добавлено нативное действие `system_which`; проверка PATH вернула путь `python.exe`. |
| `screen.snapshot` | Доступно | В JARVIS есть снимок окна/экрана (`window_snapshot`) и работа с кадрами. |
| `tts.*`, `stt.*` | Функции Companion опубликованы; Windows Node выключен | JARVIS использует собственные голосовые действия и Android audio WebSocket. |
| `device.info`, `device.status` | Companion публиковал состояние устройства | В JARVIS есть `android_device`, `android_control`, `system_monitor` и `system_diagnostics`. |
| `system.execApprovals.*` | Это настройки разрешений на команды OpenClaw Gateway | JARVIS имеет собственный интерфейс подтверждений `core/confirm.py`; Gateway и запуск команд через Node здесь были выключены. |
| `screen.record` | Согласие на запись и Node mode выключены | Не включалось и не переносилось без разрешения на запись. |
| `app.*` | Управление приложением Companion, его чатом, настройками, Gateway и сопряжением | Это функции самого OpenClaw, а не независимые инструменты JARVIS. JARVIS использует собственный чат, память, настройки и код сопряжения Android. |

Каталог Companion MCP содержал 40 инструментов; MCP-запрос использовался только для `tools/list`, действия не вызывались. Полный список: `system.notify`, `system.which`, `system.execApprovals.get`, `system.execApprovals.set`, `screen.snapshot`, `screen.record`, `tts.speak`, `tts.status`, `stt.transcribe`, `stt.listen`, `stt.status`, `device.info`, `device.status`, `app.navigate`, `app.status`, `app.sessions`, `app.agents`, `app.nodes`, `app.config.get`, `app.settings.get`, `app.settings.set`, `app.menu`, `app.search`, `app.dashboard.url`, `app.chat.snapshot`, `app.chat.send`, `app.chat.reset`, `app.chat.queue.list`, `app.chat.queue.cancel`, `app.connection.status`, `app.connection.gateways`, `app.connection.applySetupCode`, `app.connection.connectSharedToken`, `app.connection.pendingApprovals`, `app.connection.approveDevicePairing`, `app.connection.rejectDevicePairing`, `app.connection.approveNodePairing`, `app.connection.rejectNodePairing`, `app.connection.reconnect`, `app.connection.reconnectNode`.

## Проверка Android до подключения

Статус этого раздела был актуален на 20.10.2026, 15:03: тогда ADB не видел планшет и установка приложения на реальном устройстве ещё не выполнялась. Планшет подключён позже; актуальное состояние, исправление и результаты последующей проверки записаны в разделе «Обновление Android — 20.10.2026, 17:18» ниже.

## Остаток конфигурации

Переменная пользователя `JARVIS_OPENCLAW_MCP_AUTHORIZATION` всё ещё задана, но активная конфигурация JARVIS на неё не ссылается, а приложение удалено. Ранее автоматическая проверка разрешений отклонила попытку удалить её из окружения; значение не выводилось и не менялось. Для удаления переменной требуется разрешённый системный путь; обходить блокировку нельзя.

Локальный Gateway OpenClaw до удаления не слушал порт 18789, Windows Node был выключен, локальных файлов моделей не было. Камера, геолокация, browser proxy и запуск команд Companion также были выключены. Удаление Companion не затронуло активную конфигурацию JARVIS.

## Обновление Android — 20.10.2026, 17:18 (Europe/Moscow)

Пользователь подключил планшет OUKITEL RT2 (Android 12). Обновление `com.jarvis.remote` выполнено через `adb install -r`, данные приложения сохранены. Финальная сборка имеет `versionCode=2`, `versionName=1.1`; установка — 20.10.2026, 17:21. Для совместимости использован уже установленный на ПК ключ подписи JARVIS; отпечаток APK совпал с установленным приложением.

При сквозной проверке обнаружено и исправлено несовпадение криптографии: Android выводил AES-ключ через одиночный SHA-256, тогда как активный сервер использует PBKDF2-HMAC-SHA256 с солью `JARVIS-DASHBOARD-v1` и 200 000 итераций. Из-за этого сервер молча отбрасывал зашифрованные текстовые команды. `JarvisNet.kt` теперь использует тот же PBKDF2; `:app:assembleDebug` завершился успешно, исправленный APK установлен поверх приложения с сохранением его данных.

После установки приложение автоматически переподключилось к ПК; разрешения микрофона и камеры уже выданы. Голосовой канал отображал активное состояние, камера была включена повторно после обновления: планшет показал живой предпросмотр, а JARVIS подтвердил запуск видеоканала. Исправленный APK был повторно установлен как `1.1` после изменения версии. Полный ответ на текстовую команду после исправления шифрования не подтверждён: во время финальной проверки планшет перешёл в другое приложение, поэтому его не переключал обратно и не прерывал работу. Для закрытия этого пункта нужно открыть JARVIS после завершения текущего использования планшета и проверить одну короткую команду.
