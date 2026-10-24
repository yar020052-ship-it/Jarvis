"""Ограниченная автономная диагностика и обслуживание JARVIS.

Фоновый цикл работает только после подтверждённого простоя Windows: проверяет
ошибки, запускает ограниченную автопочинку и сбор метрик, ищет проекты на
GitHub. Найденный код не встраивается автоматически. Это не обучение на опыте;
метрики не меняют исходный код автоматически.
"""
from __future__ import annotations

import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "logs" / "autonomy"

# Что автономности разрешено менять. Всё остальное (конфиги с ключами,
# память, сессии, её собственное состояние) — только чтение.
EDITABLE_PREFIXES = ("actions/", "plugins/", "dashboard/", "autonomy/")
EDITABLE_SUFFIXES = (".py", ".html", ".md", ".txt", ".json")
PROTECTED_NAMES = {"api_keys.json", "credentials.json", "briefing_state.json"}


def is_editable(rel: str) -> bool:
    rel = (rel or "").replace("\\", "/")
    if not rel or "\x00" in rel or rel.startswith("/"):
        return False
    parts = rel.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        return False
    name = rel.rsplit("/", 1)[-1]
    if name in PROTECTED_NAMES:
        return False
    if not rel.endswith(EDITABLE_SUFFIXES):
        return False
    return rel.startswith(EDITABLE_PREFIXES)
