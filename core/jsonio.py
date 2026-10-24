"""Извлечение JSON-объекта из ответа модели.

Модели иногда заворачивают JSON в ```json-fence, добавляют пояснения до и
после него или возвращают несколько объектов подряд. Жадный шаблон
``re.search(r"\\{.*\\}", text, re.S)`` в этом случае захватывает лишний текст,
``json.loads`` падает, и вызывающий код решает, что JSON нет. Для игрового
пилота это означает холостой тик: план не получен, действия не выполнены.

Здесь используется ``JSONDecoder.raw_decode``: он читает ровно один объект и
не требует, чтобы строка заканчивалась на нём. Кандидаты перебираются от
содержимого fence к целому ответу и к каждой позиции ``{`` — так находится
валидный объект даже после пояснений и «мусорных» фигурных скобок в тексте.
"""

from __future__ import annotations

import json
import re
from typing import Any

_DECODER = json.JSONDecoder()
_MAX_CANDIDATES = 1024


def _strip_fence(text: str) -> str | None:
    """Вернуть содержимое первого markdown-fence без языкового тега."""
    start = text.find("```")
    if start < 0:
        return None
    body = text[start + 3:]
    first_line, separator, rest = body.partition("\n")
    # "```json\n..." — первая строка только тег; "```{...}```" — уже JSON.
    if separator and re.fullmatch(r"[A-Za-z0-9_+.-]*", first_line.strip()):
        body = rest
    end = body.find("```")
    if end >= 0:
        body = body[:end]
    body = body.strip()
    return body or None


def _candidates(text: str) -> list[str]:
    """Кандидаты на разбор: fence, весь текст и каждый фрагмент с ``{``."""
    raw = text.strip().lstrip("\ufeff")
    if not raw:
        return []
    candidates: list[str] = []
    fenced = _strip_fence(raw)
    if fenced:
        candidates.append(fenced)
    candidates.append(raw)
    for index, char in enumerate(raw):
        if char != "{":
            continue
        candidates.append(raw[index:])
        if len(candidates) >= _MAX_CANDIDATES:
            break
    return candidates


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Достать первый корректный JSON-объект из ответа модели.

    Возвращает ``None``, если валидного объекта нет. Не пытается «починить»
    обрезанный JSON: неполный план опаснее повторного запроса.
    """
    for candidate in _candidates(str(text or "")):
        chunk = candidate.strip()
        if not chunk:
            continue
        try:
            value, _end = _DECODER.raw_decode(chunk)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None
