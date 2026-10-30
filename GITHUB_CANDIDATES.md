# GITHUB_CANDIDATES.md

Реестр репозиториев, рассмотренных для заимствования кода или идей.  
Источники: `autonomy.scout` (автоматически скачанные фрагменты в `vendor_scout/`), `actions/github_scout.py` и ручной разбор.

Каждая карточка содержит: задача, лицензия, активность, совместимость, риски, статус, коммит/проверка.

## Сводная таблица

| # | Репозиторий | Лицензия | Статус | Интеграция в JARVIS |
|---|---|---|---|---|
| 1 | `coolcode/tomato-clock` | MIT | Принят | `actions/scout_coolcode_tomato_clock.py` -> `start_pomodoro_timer` |
| 2 | `GauravSingh9356/J.A.R.V.I.S` | MIT | Принят (фрагмент) | `actions/scout_gauravsingh9356_j_a_r_v_i_s.py` -> `search_youtube` |
| 3 | `alexprengere/currencyconverter` | Apache-2.0 | Принят (фрагмент) | `actions/scout_alexprengere_currencyconverter.py` -> `convert_currency` |
| 4 | `AegeanWorks/NeuroCore` | AGPL-3.0 | Только справка | Не интегрирован; идеи для будущих модулей |

## Карточки

### 1. coolcode/tomato-clock

- **Проблема:** нужен простой таймер Pomodoro для голосовых команд.
- **Источник:** `vendor_scout/coolcode-tomato-clock/tomato.py`.
- **Лицензия:** MIT (подтверждено через GitHub API).
- **Активность:** 528 stars, последний push 2024-09-23, не архивирован.
- **Совместимость:** чистый Python, работает на Windows, не тянет тяжёлые зависимости.
- **Риски:** CLI-ориентированный код с `time.sleep` и уведомлениями; при переносе в JARVIS нужно избегать блокировки UI.
- **Статус:** принят, обёртка в `actions/scout_coolcode_tomato_clock.py`.
- **Проверка:** модуль импортируется и запускается; в журнале `logs/autonomy/journal/2026-10-16.md` зафиксировано принятие.

### 2. GauravSingh9356/J.A.R.V.I.S

- **Проблема:** быстрый поиск/открытие видео на YouTube.
- **Источник:** `vendor_scout/gauravsingh9356-j.a.r.v.i.s/youtube.py`.
- **Лицензия:** MIT (подтверждено через GitHub API).
- **Активность:** 1426 stars, последний push 2025-12-02, не архивирован.
- **Совместимость:** использует `webbrowser`, работает на Windows.
- **Риски:** исходный репозиторий тянет много необязательных зависимостей (pyttsx3, pyautogui, psutil и т. д.), но в JARVIS взят только `youtube.py`; GUI-компоненты (Tkinter) не переносились.
- **Статус:** принят фрагмент, обёртка в `actions/scout_gauravsingh9356_j_a_r_v_i_s.py`.
- **Проверка:** `search_youtube` открывает браузер; детали в `logs/autonomy/journal/2026-10-19.md`.

### 3. alexprengere/currencyconverter

- **Проблема:** конвертация валют для голосовых запросов.
- **Источник:** `vendor_scout/alexprengere-currencyconverter/currency_converter.py`.
- **Лицензия:** Apache-2.0 (подтверждено через GitHub API).
- **Активность:** 242 stars, последний push 2026-10-25, не архивирован.
- **Совместимость:** чистый Python, сетевой источник данных ECB.
- **Риски:** сетевая зависимость и формат данных ECB; при недоступности сети функция должна возвращать ошибку, а не падать.
- **Статус:** принят фрагмент, обёртка в `actions/scout_alexprengere_currencyconverter.py`.
- **Проверка:** `convert_currency` возвращает результат; детали в `logs/autonomy/journal/2026-10-20.md`.

### 4. AegeanWorks/NeuroCore

- **Проблема:** модульный AI-агент с потоками (flows), долгой памятью, RAG и плагинами.
- **Источник:** `vendor_scout/AegeanWorks-NeuroCore/` (shallow clone).
- **Коммит:** `77178a1b46c5c8b66483f7bf2553268ec7202b40` от 2026-10-25.
- **Лицензия:** AGPL-3.0 (подтверждено через `LICENSE` и GitHub API).
- **Активность:** 8 stars, последний push 2026-10-26, статус alpha, 225 тестов.
- **Технологии:** Python 3.12+, FastAPI, HTMX, Tailwind, FAISS, scipy, pydantic.
- **Совместимость:** архитектурно несовместим с JARVIS (веб-приложение вместо PyQt-десктопа), но отдельные идеи (flow editor, guardrails, goal-gated changes, error budgets) полезны.
- **Риски:**
  - AGPL-3.0 — копилефтная лицензия; прямое встраивание кода потребует открытия всего проекта под AGPL.
  - тяжёлые зависимости (FAISS, scipy) увеличат размер сборки;
  - дублирование уже существующих подсистем JARVIS (память, планировщик, инструменты).
- **Статус:** только справка, не интегрирован. Дальнейшая работа — изучение архитектурных идей без копирования кода.
- **Проверка:** `vendor_scout/AegeanWorks-NeuroCore` клонирован, лицензия подтверждена, тесты не запускались (изоляция от основного проекта).

## Правила добавления новых кандидатов

1. Скачивать только в `vendor_scout/<slug>/`, не в основной код.
2. Фиксировать лицензию и коммит в этой таблице до любых изменений.
3. Для кода с копилефтными лицензиями (AGPL, GPL) — только идеи, не копирование.
4. После принятия — создать обёртку в `actions/scout_<slug>.py` и запись в `logs/autonomy/adopted.json`.
5. Всегда запускать `pytest` после добавления обёртки.
