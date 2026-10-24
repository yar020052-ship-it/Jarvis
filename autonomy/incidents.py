"""Инциденты: что сломалось, в каком статусе, сколько попыток было."""
from __future__ import annotations

import json
import time
import uuid

from . import STATE_DIR

FILE = STATE_DIR / "incidents.jsonl"
MAX_ATTEMPTS = 3


def _load() -> list[dict]:
    if not FILE.exists():
        return []
    out = []
    for line in FILE.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _save(items: list[dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    FILE.write_text(
        "\n".join(json.dumps(i, ensure_ascii=False) for i in items) + ("\n" if items else ""),
        encoding="utf-8",
    )


def add(kind: str, error: str, where: str = "", tb: str = "") -> dict:
    items = _load()
    # Дедупликация: открытый такой же инцидент не плодим
    for inc in items:
        if inc.get("status") == "open" and inc.get("where") == where and inc.get("error") == error:
            return inc
    inc = {"id": uuid.uuid4().hex[:12], "ts": time.time(), "kind": kind,
           "error": error[:500], "where": where, "tb": tb[-3000:],
           "status": "open", "attempts": 0, "note": ""}
    items.append(inc)
    _save(items)
    return inc


def open_items() -> list[dict]:
    return [i for i in _load() if i.get("status") == "open"]


def update(inc_id: str, **fields) -> None:
    items = _load()
    for inc in items:
        if inc.get("id") == inc_id:
            inc.update(fields)
    _save(items)


def all_items(limit: int = 50) -> list[dict]:
    return _load()[-limit:]
