"""Проверка обновлений Jarvis изнутри.

Источник по умолчанию — HTTPS. Раньше здесь спокойно принимались file:// и
http://, а манифест обновления — это список «что скачать и запустить», так что
подмена его по нешифрованному каналу или локальному пути превращалась в
произвольный код. Небезопасный источник теперь требует явного opt-in через
JARVIS_ALLOW_INSECURE_MANIFEST=1 (для разработки и локальных тестов).
"""

import os

try:
    from jarvis.core.update_source import check_for_update, is_newer, APP_VERSION
except Exception:
    from jarvis.core.update_source import check_for_update, is_newer, APP_VERSION  # type: ignore

MANIFEST_URL = os.environ.get("JARVIS_UPDATE_MANIFEST") or os.environ.get("JARVIS_UPDATE_URL") or "https://github.com/yar020052-ship-it/Jarvis/releases/latest/download/latest.json"


def _manifest_problem(url: str) -> str:
    """'' если источник допустим, иначе причина отказа."""
    u = (url or "").strip()
    if not u:
        return "Источник обновлений не настроен (JARVIS_UPDATE_MANIFEST)."
    if u.lower().startswith(("http://", "file://")):
        if os.environ.get("JARVIS_ALLOW_INSECURE_MANIFEST") == "1":
            return ""  # владелец явно разрешил небезопасный источник
        return (
            f"Источник обновлений '{u.split(':', 1)[0]}://' небезопасен: манифест "
            "нужно проверять по HTTPS, иначе его можно подменить. Для локальной "
            "разработки явно задай JARVIS_ALLOW_INSECURE_MANIFEST=1."
        )
    return ""


def update_check(parameters: dict | None = None, **_kwargs) -> str:
    try:
        problem = _manifest_problem(MANIFEST_URL)
        if problem:
            return problem
        info = check_for_update(MANIFEST_URL)
        if not info:
            return "Не удалось проверить обновления. Проверьте путь/доступ к манифесту."
        if is_newer(info.version, APP_VERSION):
            return f"Доступно обновление Jarvis v{info.version}\n\n{info.changelog_ru or info.changelog}\n\nМанифест: {MANIFEST_URL}"
        return f"У вас последняя версия Jarvis v{APP_VERSION}. Обновлений нет."
    except Exception as e:
        return f"Ошибка проверки обновлений: {e}"


TOOL = {
    "name": "update_check",
    "description": "Проверяет наличие новой версии Jarvis по манифесту (только HTTPS; локальный путь — только с JARVIS_ALLOW_INSECURE_MANIFEST=1).",
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    "handler": update_check,
}
