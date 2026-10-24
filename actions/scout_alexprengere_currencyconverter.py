"""Автовстройка с GitHub (пакет autonomy.scout). Не править руками: перегенерируется при следующей встройке.
Источник: vendor_scout/alexprengere-currencyconverter/currency_converter.py
"""
from __future__ import annotations

VENDOR_FILE = 'vendor_scout/alexprengere-currencyconverter/currency_converter.py'


def _load():
    """Ленивая загрузка вендорного файла (при рестарте не импортируется)."""
    import importlib.util
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent
    path = base / VENDOR_FILE
    spec = importlib.util.spec_from_file_location('scout_vendored_alexprengere-currencyconverter', str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def convert_currency(parameters=None, **_ctx) -> str:
    try:
        converter = _load()
        # Пример: конвертация 100 USD в EUR на последнюю доступную дату
        result = converter.convert(100, 'USD', 'EUR')
        return f"Результат конвертации: {result} EUR"
    except Exception as e:
        return f"Не удалось выполнить конвертацию валюты: {str(e)}"


TOOL = {
    "name": 'convert_currency',
    "description": 'Конвертирует валюты с использованием исторических данных Европейского центрального банка, когда нужно перевести сумму из одной валюты в другую',
    "parameters": {"type": "OBJECT", "properties": {
        "text": {"type": "STRING", "description": "входной текст/запрос"},
    }, "required": []},
    "handler": convert_currency,
}
