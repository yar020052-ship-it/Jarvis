"""Дневник развития простым русским: что починил, что добавил, насколько быстрее."""
from __future__ import annotations

import json
import time
from datetime import datetime

from . import STATE_DIR, is_editable  # noqa: F401  (is_editable не нужен, для API)

JOURNAL_DIR = STATE_DIR / "journal"
EVENTS_FILE = STATE_DIR / "journal.jsonl"

KINDS_RU = {
    "fix": "Починил",
    "fail": "Не вышло",
    "feature": "Добавил из GitHub",
    "perf": "Скорость",
    "health": "Проверка",
    "cycle": "Цикл",
    "note": "Заметка",
}


def _append_jsonl(entry: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def add(kind: str, text: str) -> None:
    """Записать событие в дневник (markdown по дням + jsonl)."""
    kind = kind if kind in KINDS_RU else "note"
    day = datetime.now().strftime("%Y-%m-%d")
    tm = datetime.now().strftime("%H:%M")
    _append_jsonl({"ts": time.time(), "day": day, "kind": kind, "text": text})
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    path = JOURNAL_DIR / f"{day}.md"
    if not path.exists():
        path.write_text(f"# Дневник JARVIS — {day}\n", encoding="utf-8")
    section = f"### {KINDS_RU[kind]}"
    body = path.read_text(encoding="utf-8")
    line = f"- **{tm}** — {text}\n"
    if section in body:
        body = body.replace(section, section + "\n" + line, 1)
    else:
        body = body.rstrip("\n") + f"\n\n{section}\n{line}"
    path.write_text(body, encoding="utf-8")


def today_text() -> str:
    day = datetime.now().strftime("%Y-%m-%d")
    path = JOURNAL_DIR / f"{day}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"# Дневник JARVIS — {day}\n\nПока пусто.\n"


def recent_entries(limit: int = 20) -> list[dict]:
    if not EVENTS_FILE.exists():
        return []
    out = []
    for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out
