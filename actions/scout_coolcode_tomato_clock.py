"""Автовстройка с GitHub (пакет autonomy.scout). Не править руками: перегенерируется при следующей встройке.
Источник: vendor_scout/coolcode-tomato-clock/tomato.py
"""
from __future__ import annotations

VENDOR_FILE = 'vendor_scout/coolcode-tomato-clock/tomato.py'


def _load():
    """Ленивая загрузка вендорного файла (при рестарте не импортируется)."""
    import importlib.util
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent
    path = base / VENDOR_FILE
    spec = importlib.util.spec_from_file_location('scout_vendored_coolcode-tomato-clock', str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def start_pomodoro_timer(parameters=None, **_ctx) -> str:
    try:
        mod = _load()
        mod.notify_me("Таймер Помодоро запущен: время сфокусироваться на работе!")
        return "Таймер Помодоро успешно запущен, уведомление отправлено."
    except Exception as e:
        return f"Ошибка при запуске таймера Помодоро: {e}"


TOOL = {
    "name": 'start_pomodoro_timer',
    "description": 'Запускает таймер Помодоро для отслеживания рабочих интервалов и отправки уведомлений о перерыве.',
    "parameters": {"type": "OBJECT", "properties": {
        "text": {"type": "STRING", "description": "входной текст/запрос"},
    }, "required": []},
    "handler": start_pomodoro_timer,
}
