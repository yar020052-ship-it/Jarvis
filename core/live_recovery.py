"""Классификация ошибок Live-сессии.

Отдельный модуль без тяжёлых зависимостей: main.py импортирует его на старте,
а тесты проверяют решение без поднятия Gemini-клиента.
"""

from __future__ import annotations

# Ошибка, после которой повторять сохранённый resume-handle бессмысленно:
# сервер его больше не принимает. Gemini Live закрывает сокет кодом 1008 с
# текстом «already exists», когда handle уже занят или протух. Если такой
# ответ не распознать, мёртвый handle отправляется при каждом реконнекте и
# ассистент никогда не поднимается.
_DEAD_HANDLE_MARKERS = (
    "session already exists",
    "handle expired",
    "handle not found",
    "resumption handle",
    "resume handle",
    "expired handle",
    "invalid_argument",
    "invalid argument",
    "1008",
    "already exists",
    "not_found",
    "not found",
)

# Ошибки квоты/доступности не означают, что handle мёртв: повторять его
# можно, как только лимит сбросится. Такие ошибки проверяются раньше маркеров
# «мёртвого handle», чтобы широкие подстроки вроде "handle" не давали ложный
# положительный результат.
_QUOTA_MARKERS = (
    "429",
    "resource_exhausted",
    "resource exhausted",
    "quota",
    "rate limit",
    "rate_limit",
    "too many requests",
    "retry after",
)


def is_dead_resume_handle(error: BaseException | str) -> bool:
    """True, если ошибка означает «этот resume-handle больше не работает»."""
    seen: set[int] = set()

    def _texts(err: object) -> list[str]:
        if id(err) in seen:
            return []
        seen.add(id(err))
        result = [str(err)]
        nested = getattr(err, "exceptions", None)
        if nested:
            for sub in nested:
                result.extend(_texts(sub))
        cause = getattr(err, "__cause__", None) or getattr(err, "__context__", None)
        if isinstance(cause, BaseException):
            result.extend(_texts(cause))
        return result

    if isinstance(error, str):
        texts = [error]
    else:
        texts = _texts(error)
    joined = "\n".join(texts).lower()
    if any(marker in joined for marker in _QUOTA_MARKERS):
        return False
    return any(marker in joined for marker in _DEAD_HANDLE_MARKERS)