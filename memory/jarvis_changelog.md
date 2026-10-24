# Изменения JARVIS

## 9 октября 2026

- Исправлена маршрутизация Android-команд: добавлены запуск приложений и управление экраном через ADB; перезагрузка разрешена только по прямой просьбе пользователя.

## 8 октября 2026

- Закреплён русский язык для ответов, распознавания речи и основных элементов интерфейса; для синтеза речи Edge TTS выбран русский голос.
- Исправлен путь к системному промпту `core/promt.txt`. Теперь JARVIS загружает языковые правила и динамический список своих инструментов.
- Добавлен инструмент Android через ADB/Fastboot: сведения об устройстве, перезагрузка, прошивка разрешённых разделов из `.img` и передача OTA `.zip` через sideload. Операции с перезагрузкой и прошивкой требуют подтверждения в интерфейсе.
- Добавлено правило связывать короткие ответы вроде «да» и «проверь» с последним вопросом или предложением ассистента.
- Добавлен инструмент чтения этого журнала, чтобы JARVIS отвечал о своих изменениях по записям, а не утверждал, что обновляется самостоятельно.
- Список новостей перед выводом переводится на русский язык; ссылки на источники сохраняются без изменений.
- Добавлено чтение и переключение схем электропитания Windows на максимальную производительность, сбалансированный режим и экономию энергии; смену можно отменить.
- Ярлык и файл запуска JARVIS настроены на запуск без видимого окна консоли.
- Добавлен сценарий создания загрузочной USB-флешки: поиск образа ОС на официальном сайте, SHA-256 проверка, установка Ventoy с официального архива и копирование ISO. Перед очисткой конкретного USB-диска требуется подтверждение; системные диски и неподтверждённые образы блокируются.
- Для загрузочных ISO разрешены также публичные сторонние HTTPS-источники по явному запросу пользователя. JARVIS показывает источник и предупреждает, что совпадение SHA-256 подтверждает целостность файла, но не издателя; перед очисткой выбранной флешки всё равно требуется отдельное подтверждение.
- Добавлена проверка публичного журнала обновлений Codex с отметкой последней просмотренной даты. JARVIS может сообщать новые записи и обсуждать идеи, подходящие его архитектуре; код и внешние файлы обновления автоматически не устанавливаются.
- Добавлен поиск публичных GitHub-репозиториев по теме с фильтрами языка и минимального числа звёзд, датой обновления и лицензией. Инструмент ничего не скачивает и не запускает; найденные проекты нужно отдельно проверять перед переносом.
# 2026-10-09

- Made webcam requests explicit in the vision tool and system prompt: object, scene, person, and legible-text recognition requests now route to a camera frame, with a clear one-frame limit and no background monitoring.
- Webcam capture now retries other camera indices if the saved index is stale and returns a clear Windows connection/privacy diagnostic when no camera can be opened.
- Added Android ADB Wireless debugging pairing, connect, and disconnect actions for same-network Android 11+ control without a USB cable. Pairing codes are redacted from action output; endpoints are restricted to private IPv4 addresses, and the user must authorize the session on Android.
- Added a read-only Android security check for a CPU process snapshot, enabled accessibility services, notification listeners, active device administrators, and apps without a Play Store installer marker. The response cautions that these are indicators, not a malware verdict; JARVIS must not disable or remove apps automatically.
- Added an explicit system-prompt routing rule so miner/malware checks use `android_device` action `security_check` rather than visual interpretation of the current screen. Fixed CPU process parsing for the tablet's `top` output format.
