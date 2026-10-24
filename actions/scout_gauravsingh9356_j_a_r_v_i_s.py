"""Автовстройка с GitHub (пакет autonomy.scout). Не править руками: перегенерируется при следующей встройке.
Источник: vendor_scout/gauravsingh9356-j.a.r.v.i.s/youtube.py
"""
from __future__ import annotations

VENDOR_FILE = 'vendor_scout/gauravsingh9356-j.a.r.v.i.s/youtube.py'


def _load():
    """Ленивая загрузка вендорного файла (при рестарте не импортируется)."""
    import importlib.util
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent
    path = base / VENDOR_FILE
    spec = importlib.util.spec_from_file_location('scout_vendored_gauravsingh9356-j.a.r.v.i.s', str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def search_youtube(parameters=None, **_ctx) -> str:
    p = parameters or {}
    try:
        mod = _load()
        mod.youtube(p.get("text") or "")
        return "Запрос на YouTube успешно выполнен."
    except Exception as e:
        return f"Не удалось выполнить поиск на YouTube: {e}"


TOOL = {
    "name": 'search_youtube',
    "description": 'Выполняет поиск видео на YouTube по запросу. Используется, когда пользователь просит найти видео или открыть результаты поиска на YouTube.',
    "parameters": {"type": "OBJECT", "properties": {
        "text": {"type": "STRING", "description": "входной текст/запрос"},
    }, "required": []},
    "handler": search_youtube,
}
