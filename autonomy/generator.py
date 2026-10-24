"""Генерация исправлений через Gemini (ключ уже настроен у владельца).

Модель получает файл + ошибку + фидбек прошлой попытки и возвращает
ИСПРАВЛЕННЫЙ ФАЙЛ ЦЕЛИКОМ в блоке:
    FILE: <путь>
    ```python
    <содержимое>
    ```
Без модели (нет ключа) — честная ошибка, чиним руками.
"""
from __future__ import annotations

import re
from core import gemini

_FILE_BLOCK = re.compile(
    r"^FILE:\s*(?P<rel>[^\n]+)\s*\n+```[a-zA-Z]*\n(?P<body>.*?)```",
    re.S | re.M)
_ANY_FENCE = re.compile(r"```[a-zA-Z]*\n(?P<body>.*?)```", re.S)

SYSTEM = (
    "Ты — автономный ремонтник JARVIS, чинишь Python-код (Windows, "
    "стандартная библиотека + установленные зависимости). "
    "Отвечаешь СТРОГО в формате: строка FILE: <путь>, затем весь "
    "исправленный файл в блоке кода. Без пояснений вне формата. "
    "Не урезай файл, не меняй поведение кроме исправления ошибки.")

def gen_text(contents: list[str], system: str = "", tier: str | None = None) -> str:
    """Generate text through JARVIS's central task-aware Gemini router."""
    profile = tier or gemini.tier_for_task("general")
    parts = ([system] if system else []) + list(contents)
    response = gemini.call(parts, tier=profile, timeout_ms=120_000)
    text = (getattr(response, "text", "") or "").strip() if response else ""
    if not text:
        raise RuntimeError(f"Все модели Gemini для профиля {profile} недоступны.")
    return text


def generate_fix(rel: str, content: str, error: str, tb: str = "",
                 feedback: str = "") -> tuple[str, str]:
    """Возвращает (rel, новый_текст). Бросает RuntimeError при неудаче."""
    prompt = (
        f"ОШИБКА: {error}\nМЕСТО: {rel}\n"
        + (f"TRACEBACK:\n{tb[-2000:]}\n" if tb else "")
        + (f"ПРОШЛАЯ ПОПЫТКА НЕ ПРОШЛА ПРОВЕРКУ:\n{feedback}\n" if feedback else "")
        + f"СОДЕРЖИМОЕ ФАЙЛА {rel}:\n```python\n{content[-30000:]}\n```\n"
        "Верни ТОЛЬКО исправленный файл целиком в формате:\n"
        f"FILE: {rel}\n```python\n<полное содержимое>\n```"
    )
    text = gen_text([prompt], system=SYSTEM, tier=gemini.tier_for_task("repair"))
    m = _FILE_BLOCK.search(text)
    if m:
        return m.group("rel").strip(), m.group("body")
    m2 = _ANY_FENCE.search(text)
    if m2:
        return rel, m2.group("body")
    raise RuntimeError("Модель вернула не формат (нет блока с файлом).")
