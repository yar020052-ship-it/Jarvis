"""JARVIS Packs — готовые паки команд (как 48 паков Luxify).

Пак — это набор команд с фразами и шагами. Ставится одной командой:
    python jarvis_commands.py install windows

Правила:
  * команда, которую пользователь изменил, при обновлении пака не трогается;
  * «key» уникален внутри пака и связывает версии при обновлении;
  * фразы короткие, по-русски; английские — синонимами.

Типы шагов — см. `jarvis_commands.run_step` и AI_SCHEMA_HINT.
"""
from __future__ import annotations


def _cmd(key: str, name: str, phrases: list, steps: list,
         reply: str = "", confirm: bool | None = None) -> dict:
    d = {"key": key, "name": name, "phrases": phrases, "steps": steps,
         "reply": reply}
    if confirm is not None:
        d["confirm"] = confirm
    return d


PACKS: dict[str, dict] = {}

# --------------------------------------------------------------------------- #
#  Windows — система, звук, окна, клавиши
# --------------------------------------------------------------------------- #
PACKS["windows"] = {
    "name": "Windows",
    "version": 1,
    "note": "звук, окна, питание, буфер обмена, диктовка",
    "commands": [
        _cmd("vol_up", "Громче", ["громче", "сделай громче", "прибавь звук",
                                  "прибавь громкость"],
             [{"type": "volume", "mode": "up", "times": 3}]),
        _cmd("vol_down", "Тише", ["тише", "сделай тише", "убавь звук",
                                  "потише", "уменьши громкость"],
             [{"type": "volume", "mode": "down", "times": 3}]),
        _cmd("vol_mute", "Выключить звук", ["выключи звук", "заглуши звук",
                                            "мьют", "mute"],
             [{"type": "volume", "mode": "mute"}]),
        _cmd("vol_50", "Звук наполовину", ["звук наполовину", "громкость пятьдесят"],
             [{"type": "volume", "mode": "set", "level": 50}]),
        _cmd("vol_max", "Звук на максимум", ["звук на максимум",
                                             "громкость на максимум"],
             [{"type": "volume", "mode": "set", "level": 100}]),
        _cmd("vol_min", "Звук на минимум", ["звук на минимум"],
             [{"type": "volume", "mode": "set", "level": 5}]),

        _cmd("desktop", "Свернуть все окна", ["сверни все окна",
                                              "покажи рабочий стол"],
             [{"type": "minimize_all"}]),
        _cmd("taskview", "Представление задач", ["представление задач"],
             [{"type": "task_view"}]),
        _cmd("alt_tab", "Переключить окно", ["переключи окно",
                                             "следующее окно"],
             [{"type": "alt_tab"}]),
        _cmd("win_min", "Свернуть окно", ["сверни окно"],
             [{"type": "hotkey", "keys": "win+down"}]),
        _cmd("win_max", "Развернуть окно", ["разверни окно", "окно на весь экран"],
             [{"type": "hotkey", "keys": "win+up"}]),
        _cmd("close_window", "Закрыть окно", ["закрой окно"],
             [{"type": "hotkey", "keys": "alt+f4"}]),
        _cmd("fullscreen", "Полный экран", ["полный экран", "во весь экран"],
             [{"type": "key", "key": "f11"}]),

        _cmd("lock", "Заблокировать ПК", ["заблокируй компьютер",
                                          "заблокируй экран", "я ушёл"],
             [{"type": "lock"}]),
        _cmd("monitor_off", "Выключить экран", ["выключи экран",
                                                "погаси монитор"],
             [{"type": "monitor_off"}]),
        _cmd("sleep", "Спящий режим", ["спящий режим", "усни"],
             [{"type": "sleep"}], confirm=True),
        _cmd("hibernate", "Гибернация", ["гибернация"],
             [{"type": "hibernate"}], confirm=True),
        _cmd("shutdown", "Выключить компьютер", ["выключи компьютер",
                                                 "заверши работу пк",
                                                 "выключи пк"],
             [{"type": "shutdown"}], confirm=True),
        _cmd("restart", "Перезагрузить компьютер", ["перезагрузи компьютер",
                                                    "перезагрузка"],
             [{"type": "restart"}], confirm=True),
        _cmd("shutdown_abort", "Отменить выключение", ["отмени выключение"],
             [{"type": "cmd", "command": "shutdown /a"}]),

        _cmd("explorer", "Открыть проводник", ["открой проводник",
                                               "мой компьютер"],
             [{"type": "launch", "target": "explorer"}]),
        _cmd("settings", "Параметры Windows", ["открой параметры",
                                               "настройки windows"],
             [{"type": "url", "url": "ms-settings:"}]),
        _cmd("taskmgr", "Диспетчер задач", ["открой диспетчер задач"],
             [{"type": "launch", "target": "taskmgr"}]),
        _cmd("notepad", "Блокнот", ["открой блокнот"],
             [{"type": "launch", "target": "notepad"}]),
        _cmd("calc", "Калькулятор", ["открой калькулятор"],
             [{"type": "launch", "target": "calc"}]),
        _cmd("paint", "Paint", ["открой пейнт", "открой paint"],
             [{"type": "launch", "target": "mspaint"}]),
        _cmd("screenshot_area", "Скриншот области",
             ["сделай скриншот области", "снимок области"],
             [{"type": "launch", "target": "ms-screenclip:"}]),
        _cmd("screenshot_full", "Скриншот экрана", ["сделай скриншот",
                                                    "снимок экрана"],
             [{"type": "screenshot"},
              {"type": "toast", "title": "JARVIS",
               "text": "Скриншот сохранён в папке screenshots"}]),

        _cmd("layout", "Сменить раскладку", ["смени раскладку",
                                             "переключи язык", "смени язык"],
             [{"type": "hotkey", "keys": "alt+shift"}]),
        _cmd("copy", "Копировать", ["копируй", "скопируй"],
             [{"type": "hotkey", "keys": "ctrl+c"}]),
        _cmd("paste", "Вставить", ["вставь"],
             [{"type": "hotkey", "keys": "ctrl+v"}]),
        _cmd("cut", "Вырезать", ["вырежи"],
             [{"type": "hotkey", "keys": "ctrl+x"}]),
        _cmd("undo", "Отменить", ["отмени", "отмена действия"],
             [{"type": "hotkey", "keys": "ctrl+z"}]),
        _cmd("redo", "Повторить", ["повтори действие"],
             [{"type": "hotkey", "keys": "ctrl+y"}]),
        _cmd("save", "Сохранить", ["сохрани"],
             [{"type": "hotkey", "keys": "ctrl+s"}]),
        _cmd("select_all", "Выделить всё", ["выдели всё", "выдели все"],
             [{"type": "hotkey", "keys": "ctrl+a"}]),
        _cmd("find_page", "Найти на странице", ["найди на странице"],
             [{"type": "hotkey", "keys": "ctrl+f"}]),
        _cmd("enter", "Нажать Enter", ["нажми enter", "подтверди"],
             [{"type": "key", "key": "enter"}]),
        _cmd("escape", "Нажать Escape", ["нажми escape", "закрой это"],
             [{"type": "key", "key": "esc"}]),

        _cmd("dictation", "Диктовка Windows", ["включи диктовку", "диктовка"],
             [{"type": "dictation"}]),
        _cmd("clip_read", "Прочитать буфер", ["прочитай буфер",
                                              "что в буфере"],
             [{"type": "clipboard_speak"}]),
        _cmd("clip_clear", "Очистить буфер", ["очисти буфер"],
             [{"type": "clipboard_set", "text": ""}], reply="Буфер очищен."),
    ],
}

# --------------------------------------------------------------------------- #
#  Браузер
# --------------------------------------------------------------------------- #
PACKS["browser"] = {
    "name": "Браузер",
    "version": 1,
    "note": "вкладки, навигация, поиск, масштаб",
    "commands": [
        _cmd("open", "Открыть браузер", ["открой браузер"],
             [{"type": "url", "url": "about:blank"}]),
        _cmd("new_tab", "Новая вкладка", ["новая вкладка"],
             [{"type": "hotkey", "keys": "ctrl+t"}]),
        _cmd("close_tab", "Закрыть вкладку", ["закрой вкладку"],
             [{"type": "hotkey", "keys": "ctrl+w"}]),
        _cmd("reopen_tab", "Вернуть вкладку", ["верни вкладку",
                                               "восстанови вкладку"],
             [{"type": "hotkey", "keys": "ctrl+shift+t"}]),
        _cmd("refresh", "Обновить страницу", ["обнови страницу", "обнови"],
             [{"type": "key", "key": "f5"}]),
        _cmd("back", "Назад", ["назад"], [{"type": "hotkey", "keys": "alt+left"}]),
        _cmd("forward", "Вперёд", ["страница вперёд", "вперёд"],
             [{"type": "hotkey", "keys": "alt+right"}]),
        _cmd("incognito", "Режим инкогнито", ["режим инкогнито",
                                              "открой инкогнито"],
             [{"type": "hotkey", "keys": "ctrl+shift+n"}]),
        _cmd("zoom_in", "Увеличить страницу", ["увеличь страницу"],
             [{"type": "hotkey", "keys": "ctrl+="}]),
        _cmd("zoom_out", "Уменьшить страницу", ["уменьши страницу"],
             [{"type": "hotkey", "keys": "ctrl+-"}]),
        _cmd("zoom_reset", "Сбросить масштаб", ["сбрось масштаб"],
             [{"type": "hotkey", "keys": "ctrl+0"}]),
        _cmd("search_google", "Поиск в Google",
             ["загугли {query}", "найди в гугле {query}", "погугли {query}"],
             [{"type": "search", "query": "{query}", "engine": "google"}]),
        _cmd("search_yandex", "Поиск в Яндексе", ["найди в яндексе {query}"],
             [{"type": "search", "query": "{query}", "engine": "yandex"}]),
        _cmd("open_site", "Открыть сайт", ["открой сайт {query}"],
             [{"type": "site", "query": "{query}"}]),
    ],
}

# --------------------------------------------------------------------------- #
#  Медиа (музыка и видео — общее управление)
# --------------------------------------------------------------------------- #
PACKS["media"] = {
    "name": "Медиа",
    "version": 1,
    "note": "плей/пауза, треки; понимает YouTube на экране",
    "commands": [
        _cmd("play", "Играть", ["включи музыку", "продолжи музыку",
                                "играй", "включи трек"],
             [{"type": "media", "key": "play"}]),
        _cmd("pause", "Пауза", ["пауза", "останови музыку", "поставь на паузу"],
             [{"type": "if_window", "match": "youtube",
               "steps": [{"type": "key", "key": "k"}],
               "else_steps": [{"type": "media", "key": "pause"}]}]),
        _cmd("next", "Следующий трек", ["следующий трек", "следующая песня",
                                        "переключи трек", "дальше"],
             [{"type": "if_window", "match": "youtube",
               "steps": [{"type": "hotkey", "keys": "shift+n"}],
               "else_steps": [{"type": "media", "key": "next"}]}]),
        _cmd("prev", "Предыдущий трек", ["предыдущий трек", "прошлая песня"],
             [{"type": "if_window", "match": "youtube",
               "steps": [{"type": "hotkey", "keys": "shift+p"}],
               "else_steps": [{"type": "media", "key": "prev"}]}]),
        _cmd("stop", "Стоп", ["стоп музыка", "останови трек"],
             [{"type": "media", "key": "stop"}]),
    ],
}

# --------------------------------------------------------------------------- #
#  YouTube
# --------------------------------------------------------------------------- #
PACKS["youtube"] = {
    "name": "YouTube",
    "version": 1,
    "note": "поиск, плеер, субтитры",
    "commands": [
        _cmd("open", "Открыть YouTube", ["открой ютуб", "открой youtube",
                                         "включи ютуб"],
             [{"type": "url", "url": "https://www.youtube.com"}]),
        _cmd("search", "Поиск на YouTube", ["найди на ютубе {query}",
                                            "включи на ютубе {query}",
                                            "включи песню {query}"],
             [{"type": "search", "query": "{query}", "engine": "youtube"}]),
        _cmd("seek_fwd", "Вперёд 5 секунд", ["вперёд пять секунд",
                                             "промотай вперёд"],
             [{"type": "if_window", "match": "youtube",
               "steps": [{"type": "key", "key": "right"}],
               "else_steps": [{"type": "media", "key": "next"}]}]),
        _cmd("seek_back", "Назад 5 секунд", ["назад пять секунд",
                                             "промотай назад"],
             [{"type": "if_window", "match": "youtube",
               "steps": [{"type": "key", "key": "left"}],
               "else_steps": [{"type": "media", "key": "prev"}]}]),
        _cmd("fs", "На весь экран (видео)", ["видео на весь экран"],
             [{"type": "if_window", "match": "youtube",
               "steps": [{"type": "key", "key": "f"}],
               "else_steps": [{"type": "key", "key": "f11"}]}]),
        _cmd("subs", "Субтитры", ["включи субтитры"],
             [{"type": "key", "key": "c"}]),
        _cmd("theater", "Режим кинотеатра", ["режим кинотеатра"],
             [{"type": "key", "key": "t"}]),
    ],
}

# --------------------------------------------------------------------------- #
#  Telegram
# --------------------------------------------------------------------------- #
PACKS["telegram"] = {
    "name": "Telegram",
    "version": 1,
    "note": "быстрый запуск мессенджера",
    "commands": [
        _cmd("open", "Открыть Telegram", ["открой телеграм", "открой телегу"],
             [{"type": "url", "url": "tg://"}]),
        _cmd("tg_stop", "Остановить голосового Джарвиса (в Telegram)",
             ["останови голосового джарвиса"],
             [{"type": "cmd", "command":
                "echo stop > .jarvis\\voice\\stop",
              "dangerous": False}]),
    ],
}

# --------------------------------------------------------------------------- #
#  Музыка (Spotify / Яндекс Музыка)
# --------------------------------------------------------------------------- #
PACKS["music"] = {
    "name": "Музыка",
    "version": 1,
    "note": "Spotify и Яндекс Музыка",
    "commands": [
        _cmd("sp_open", "Открыть Spotify", ["открой спотифай", "открой spotify"],
             [{"type": "url", "url": "spotify:"}]),
        _cmd("sp_search", "Включить в Spotify", ["включи в спотифае {query}",
                                                 "включи спотифай {query}"],
             [{"type": "url", "url": "spotify:search:{query}"}]),
        _cmd("ym_open", "Яндекс Музыка", ["открой яндекс музыку",
                                          "включи яндекс музыку"],
             [{"type": "url", "url": "https://music.yandex.ru"}]),
        _cmd("ym_search", "Включить в Яндекс Музыке",
             ["включи в яндекс музыке {query}"],
             [{"type": "site",
               "query": "music.yandex.ru/search?text={query}"}]),
    ],
}

# --------------------------------------------------------------------------- #
#  VS Code
# --------------------------------------------------------------------------- #
PACKS["vscode"] = {
    "name": "VS Code",
    "version": 1,
    "note": "палитра, терминал, навигация",
    "commands": [
        _cmd("open", "Открыть VS Code", ["открой вс код", "открой код",
                                         "открой vs code"],
             [{"type": "launch", "target": "code"}]),
        _cmd("palette", "Командная палитра", ["командная палитра"],
             [{"type": "hotkey", "keys": "ctrl+shift+p"}]),
        _cmd("terminal", "Терминал VS Code", ["терминал в коде", "открой терминал"],
             [{"type": "hotkey", "keys": "ctrl+`"}]),
        _cmd("sidebar", "Боковая панель", ["боковая панель"],
             [{"type": "hotkey", "keys": "ctrl+b"}]),
        _cmd("format", "Форматировать файл", ["отформатируй файл"],
             [{"type": "hotkey", "keys": "shift+alt+f"}]),
        _cmd("quick_open", "Быстрое открытие файла", ["быстрое открытие файла"],
             [{"type": "hotkey", "keys": "ctrl+p"}]),
        _cmd("problems", "Панель ошибок", ["покажи ошибки"],
             [{"type": "hotkey", "keys": "ctrl+shift+m"}]),
    ],
}

# --------------------------------------------------------------------------- #
#  Discord
# --------------------------------------------------------------------------- #
PACKS["discord"] = {
    "name": "Discord",
    "version": 1,
    "note": "микрофон и звук на горячих клавишах",
    "commands": [
        _cmd("open", "Открыть Discord", ["открой дискорд"],
             [{"type": "url", "url": "discord://"}]),
        _cmd("mute", "Мьют микрофона", ["замьють микрофон", "выключи микрофон"],
             [{"type": "hotkey", "keys": "ctrl+shift+m"}]),
        _cmd("deafen", "Заглушить Discord", ["заглуши дискорд",
                                             "выключи звук дискорда"],
             [{"type": "hotkey", "keys": "ctrl+shift+d"}]),
    ],
}

# --------------------------------------------------------------------------- #
#  Steam / игры
# --------------------------------------------------------------------------- #
PACKS["steam"] = {
    "name": "Steam",
    "version": 1,
    "note": "запуск и режим большого экрана",
    "commands": [
        _cmd("open", "Открыть Steam", ["открой стим", "запусти стим"],
             [{"type": "url", "url": "steam://open/main"}]),
        _cmd("big_picture", "Большой экран Steam", ["режим большого экрана"],
             [{"type": "url", "url": "steam://open/bigpicture"}]),
        _cmd("screenshot", "Скриншот в игре (Steam)", ["скриншот стим"],
             [{"type": "key", "key": "f12"}]),
    ],
}

# --------------------------------------------------------------------------- #
#  Office (Word / Excel)
# --------------------------------------------------------------------------- #
PACKS["office"] = {
    "name": "Office",
    "version": 1,
    "note": "Word и Excel",
    "commands": [
        _cmd("word", "Открыть Word", ["открой ворд", "открой word"],
             [{"type": "launch", "target": "winword"}]),
        _cmd("excel", "Открыть Excel", ["открой эксель", "открой excel"],
             [{"type": "launch", "target": "excel"}]),
        _cmd("new_doc", "Новый документ Word", ["создай документ ворд"],
             [{"type": "powershell",
               "script": "$w = New-Object -ComObject Word.Application; "
                         "$w.Visible = $true; $w.Documents.Add() | Out-Null"}]),
        _cmd("new_sheet", "Новая таблица Excel", ["создай таблицу excel"],
             [{"type": "powershell",
               "script": "$x = New-Object -ComObject Excel.Application; "
                         "$x.Visible = $true; $x.Workbooks.Add() | Out-Null"}]),
    ],
}

# --------------------------------------------------------------------------- #
#  ChatGPT и ИИ
# --------------------------------------------------------------------------- #
PACKS["ai"] = {
    "name": "ИИ",
    "version": 1,
    "note": "вопрос голосом, перевод, объяснение",
    "commands": [
        _cmd("open_chatgpt", "Открыть ChatGPT", ["открой чат гпт",
                                                 "открой chatgpt"],
             [{"type": "url", "url": "https://chatgpt.com"}]),
        _cmd("ask", "Спросить ИИ", ["спроси у ии {query}",
                                    "спроси у джарвиса {query}"],
             [{"type": "ask", "prompt": "{query}", "speak": True}]),
        _cmd("translate_sel", "Перевести выделенное", ["переведи выделенное"],
             [{"type": "ask", "speak": True, "prompt":
               "Переведи текст ниже и верни только перевод, без пояснений "
               "и кавычек:\n{selection}"}]),
        _cmd("explain_sel", "Объяснить выделенное", ["объясни выделенное"],
             [{"type": "ask", "speak": True, "prompt":
               "Объясни простыми словами, коротко (2-3 предложения), "
               "что это:\n{selection}"}]),
    ],
}

# --------------------------------------------------------------------------- #
#  Чтение вслух
# --------------------------------------------------------------------------- #
PACKS["reading"] = {
    "name": "Чтение",
    "version": 1,
    "note": "прочитать вслух выделенное или буфер",
    "commands": [
        _cmd("read_selection", "Прочитать выделенное", ["прочитай выделенное",
                                                        "прочитай текст"],
             [{"type": "hotkey", "keys": "ctrl+c"},
              {"type": "pause", "seconds": 0.4},
              {"type": "clipboard_speak"}]),
        _cmd("speed_read", "Быстрое чтение", ["прочитай быстро"],
             [{"type": "hotkey", "keys": "ctrl+c"},
              {"type": "pause", "seconds": 0.4},
              {"type": "clipboard_speak"}]),
    ],
}

# --------------------------------------------------------------------------- #
#  Сценарии — одна фраза, много действий
# --------------------------------------------------------------------------- #
PACKS["scenarios"] = {
    "name": "Сценарии",
    "version": 1,
    "note": "режимы на каждый день",
    "commands": [
        _cmd("good_morning", "Доброе утро", ["доброе утро"],
             [{"type": "url", "url": "tg://"},
              {"type": "pause", "seconds": 1},
              {"type": "url", "url": "https://www.google.com"},
              {"type": "volume", "mode": "set", "level": 30},
              {"type": "speak", "text": "Доброе утро, сэр. Системы готовы."}]),
        _cmd("work_mode", "Рабочий режим", ["рабочий режим"],
             [{"type": "url", "url": "tg://"},
              {"type": "url", "url": "https://mail.google.com"},
              {"type": "launch", "target": "code"},
              {"type": "volume", "mode": "set", "level": 30},
              {"type": "speak", "text": "Все системы готовы, сэр."}]),
        _cmd("game_mode", "Игровой режим", ["игровой режим", "го играть"],
             [{"type": "url", "url": "steam://open/main"},
              {"type": "url", "url": "discord://"},
              {"type": "pause", "seconds": 2},
              {"type": "volume", "mode": "set", "level": 80},
              {"type": "speak", "text": "Готово. Хорошей игры!"}]),
        _cmd("movie_mode", "Режим кино", ["режим кино", "хочу кино"],
             [{"type": "minimize_all"},
              {"type": "volume", "mode": "set", "level": 40},
              {"type": "url", "url": "https://www.youtube.com"},
              {"type": "speak", "text": "Приятного просмотра."}]),
        _cmd("guests", "Протокол «Гости»", ["протокол гости"],
             [{"type": "minimize_all"},
              {"type": "volume", "mode": "mute"},
              {"type": "url", "url": "https://www.youtube.com"},
              {"type": "speak", "text": "Протокол выполнен."}]),
        _cmd("good_night", "Спокойной ночи", ["спокойной ночи"],
             [{"type": "speak", "text": "Спокойной ночи, сэр."},
              {"type": "volume", "mode": "mute"},
              {"type": "pause", "seconds": 3},
              {"type": "sleep"}]),
        _cmd("focus_mode", "Режим сосредоточенности", ["режим тишины",
                                                       "не беспокоить"],
             [{"type": "minimize_all"},
              {"type": "volume", "mode": "set", "level": 15},
              {"type": "url", "url": "https://www.google.com"},
              {"type": "speak", "text": "Режим тишины включён."}]),
    ],
}

# --------------------------------------------------------------------------- #
# App / service launch packs. These are deliberately small, editable starter
# packs: launch targets can be changed from the command editor when an app uses
# a different executable name or install directory on the user's PC.
# --------------------------------------------------------------------------- #
_APP_PACKS = [
    ("whatsapp", "WhatsApp", "https://web.whatsapp.com", "открой ватсап", "ватсап"),
    ("vk", "ВКонтакте", "https://vk.com", "открой вк", "вконтакте"),
    ("instagram", "Instagram", "https://www.instagram.com", "открой инстаграм", "инстаграм"),
    ("zoom", "Zoom", "zoom", "открой зум", "зум"),
    ("yandex_browser", "Яндекс Браузер", "browser", "открой яндекс браузер", "яндекс браузер"),
    ("edge", "Microsoft Edge", "msedge", "открой эдж", "эдж"),
    ("opera_gx", "Opera GX", "opera", "открой опера гх", "опера гх"),
    ("firefox", "Firefox", "firefox", "открой фаерфокс", "фаерфокс"),
    ("brave", "Brave", "brave", "открой браве", "браве"),
    ("photoshop", "Photoshop", "Photoshop", "открой фотошоп", "фотошоп"),
    ("figma", "Figma", "https://www.figma.com", "открой фигму", "фигма"),
    ("canva", "Canva", "https://www.canva.com", "открой канву", "канва"),
    ("illustrator", "Adobe Illustrator", "Illustrator", "открой иллюстратор", "иллюстратор"),
    ("blender", "Blender", "blender", "открой блендер", "блендер"),
    ("lightroom", "Lightroom", "Lightroom", "открой лайтрум", "лайтрум"),
    ("animate", "Adobe Animate", "Animate", "открой анимейт", "анимейт"),
    ("obs", "OBS Studio", "obs64", "открой обс", "обс"),
    ("premiere_pro", "Adobe Premiere Pro", "Adobe Premiere Pro", "открой премьер про", "премьер про"),
    ("capcut", "CapCut", "CapCut", "открой капкат", "капкат"),
    ("davinci_resolve", "DaVinci Resolve", "Resolve", "открой давинчи", "давинчи"),
    ("after_effects", "After Effects", "AfterFX", "открой афтер эффектс", "афтер эффектс"),
    ("fl_studio", "FL Studio", "fl64", "открой фл студио", "фл студио"),
    ("audition", "Adobe Audition", "Adobe Audition", "открой адоб аудишн", "адоб аудишн"),
    ("vegas_pro", "VEGAS Pro", "vegas", "открой вегас", "вегас"),
    ("twitch", "Twitch", "https://www.twitch.tv", "открой твич", "твич"),
    ("chatgpt", "ChatGPT", "https://chatgpt.com", "открой чат джипити", "чат джипити"),
    ("claude", "Claude", "https://claude.ai", "открой клауд", "клауд"),
    ("notion", "Notion", "https://www.notion.so", "открой ноушен", "ноушен"),
    ("powerpoint", "Microsoft PowerPoint", "powerpnt", "открой поверпоинт", "поверпоинт"),
    ("chrome", "Google Chrome", "chrome", "открой гугл хром", "гугл хром"),
    ("pycharm", "PyCharm", "pycharm64", "открой пайчарм", "пайчарм"),
    ("github_desktop", "GitHub Desktop", "github", "открой гитхаб десктоп", "гитхаб десктоп"),
    ("cs2", "Counter-Strike 2", "steam://rungameid/730", "запусти кс два", "кс два"),
    ("minecraft", "Minecraft", "minecraft://", "запусти майнкрафт", "майнкрафт"),
    ("dota2", "Dota 2", "steam://rungameid/570", "запусти доту", "дота"),
    ("fortnite", "Fortnite", "com.epicgames.launcher://apps/Fortnite?action=launch", "запусти фортнайт", "фортнайт"),
    ("pubg", "PUBG", "steam://rungameid/578080", "запусти пабг", "пабг"),
    ("league_of_legends", "League of Legends", "https://www.leagueoflegends.com", "открой лигу легенд", "лига легенд"),
]

for _pid, _name, _target, _open_phrase, _alias in _APP_PACKS:
    _target_step = (
        {"type": "url", "url": _target}
        if _target.startswith(("https://", "http://", "steam://", "minecraft://", "com.epicgames."))
        else {"type": "launch", "target": _target}
    )
    PACKS[_pid] = {
        "name": _name,
        "version": 1,
        "note": "быстрый запуск и управление окном; можно настроить под установленную версию",
        "commands": [
            _cmd("open", f"Открыть {_name}", [_open_phrase, f"запусти {_alias}"],
                 [_target_step]),
            _cmd("minimize", f"Свернуть {_name}", [f"сверни {_alias}"],
                 [{"type": "window", "mode": "minimize", "match": _name}]),
            _cmd("restore", f"Вернуть {_name}", [f"верни {_alias}", f"покажи {_alias}"],
                 [{"type": "window", "mode": "focus", "match": _name}]),
            _cmd("close", f"Закрыть {_name}", [f"закрой {_alias}"],
                 [{"type": "window", "mode": "close", "match": _name}]),
        ],
    }
