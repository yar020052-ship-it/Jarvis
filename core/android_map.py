"""Постоянная карта знаний JARVIS об Android-устройствах (самообучение).

JARVIS учится, где что находится на подключённом планшете или телефоне:

* какие приложения стоят и как называются;
* какие экраны у них есть и какие элементы (подписи, resource-id) на них;
* что открывается после нажатия — переходы между экранами;
* какие пути ведут к нужным функциям («включить Wi-Fi» → шаги).

Знание переживает перезапуски JARVIS и накапливается от сессии к сессии.
Хранится по одному JSON на физическое устройство в
``<user-data>/memory/android_maps/<key>.json`` (ключ — ``ro.serialno`` или
android_id, так что Wi-Fi/USB-подключения одного и того же устройства дают
один файл).

Координаты НЕ считаются истиной: они устаревают при повороте экрана, смене
темы и обновлении приложения. Перед каждым нажатием элемент ищется заново по
подписи или resource-id на свежем дампе интерфейса, а сохранённый центр
используется только как подсказка о том, как элемент выглядел.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

_LOCK = threading.Lock()
_MAX_SCREENS = 120
_MAX_ELEMENTS_PER_SCREEN = 120
_MAX_EXITS_PER_SCREEN = 40
_MAX_FUNCTIONS = 150
_MAX_APPS = 400
_FILE_KEY_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _maps_dir() -> Path:
    try:
        from core.paths import MEMORY_DIR

        base = Path(MEMORY_DIR)
    except Exception:
        base = Path.home() / "AppData" / "Local" / "JARVIS" / "memory"
    return base / "android_maps"


def safe_key(raw: str) -> str:
    """Filesystem-safe stable device key."""
    key = _FILE_KEY_RE.sub("_", (raw or "").strip())[:64].strip("_")
    return key or "unknown"


def map_path(key: str) -> Path:
    return _maps_dir() / f"{safe_key(key)}.json"


def _blank(key: str) -> dict:
    return {"v": 1, "device": {"key": key}, "apps": {}, "screens": {}, "functions": {}}


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_device(key: str) -> dict:
    """Map of one device; a blank map when the file is missing or unreadable."""
    path = map_path(key)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("screens"), dict):
                data.setdefault("apps", {})
                data.setdefault("functions", {})
                data.setdefault("device", {"key": safe_key(key)})
                return data
        except Exception:
            pass
    return _blank(safe_key(key))


def save_device(key: str, data: dict) -> None:
    """Persist the map, pruning the oldest entries so files stay bounded."""
    prune(data)
    path = map_path(key)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)


def touch_device(data: dict, info: dict) -> None:
    """Update the device header from freshly read properties."""
    dev = data.setdefault("device", {})
    for field in ("name", "model", "android", "sdk", "screen", "locale", "serial"):
        value = str(info.get(field) or "").strip()
        if value:
            dev[field] = value
    dev["last_seen"] = _stamp()
    dev["surveys"] = int(dev.get("surveys") or 0) + 1


def record_app(data: dict, package: str, label: str = "") -> None:
    package = (package or "").strip()
    if not package:
        return
    apps = data.setdefault("apps", {})
    entry = apps.setdefault(package, {})
    if (label or "").strip():
        entry["label"] = label.strip()[:60]
    entry["last_seen"] = _stamp()


def screen_key(app: str, activity: str = "") -> str:
    """Canonical screen id: "<package>/<full.activity.Name>"."""
    app = (app or "").strip()
    act = (activity or "").strip()
    if act.startswith("."):
        act = app + act
    return f"{app}/{act}" if act else app


def record_screen(
    data: dict,
    app: str,
    activity: str,
    elements: list[dict],
    name: str = "",
    note: str = "",
) -> dict:
    """Upsert one screen with its labeled elements; returns a change summary."""
    app = (app or "").strip()
    key = screen_key(app, activity)
    screens = data.setdefault("screens", {})
    scr = screens.setdefault(
        key, {"app": app, "elements": [], "exits": {}, "first_seen": _stamp()}
    )
    if name.strip():
        scr["label"] = name.strip()[:80]
    if note.strip():
        scr["note"] = note.strip()[:200]
    if activity.strip():
        scr["activity"] = activity.strip()

    known: dict[tuple, dict] = {}
    for el in scr.get("elements") or []:
        if isinstance(el, dict):
            known[(el.get("label", ""), el.get("id", ""))] = el
    added = updated = 0
    now = _stamp()
    for el in list(elements)[: _MAX_ELEMENTS_PER_SCREEN]:
        if not isinstance(el, dict):
            continue
        label = str(el.get("label") or "")[:80]
        rid = str(el.get("id") or "")[:120]
        if not (label or rid):
            continue
        center = el.get("c")
        if not (isinstance(center, (list, tuple)) and len(center) == 2):
            continue
        center = [int(center[0]), int(center[1])]
        slot = known.get((label, rid))
        if slot is None:
            known[(label, rid)] = {
                "label": label,
                "id": rid,
                "cls": str(el.get("cls") or "")[:60],
                "clickable": bool(el.get("clickable")),
                "c": center,
                "seen": now,
            }
            added += 1
        else:
            if slot.get("c") != center or bool(slot.get("clickable")) != bool(el.get("clickable")):
                updated += 1
            slot["c"] = center
            slot["clickable"] = bool(el.get("clickable"))
            slot["seen"] = now
            if el.get("cls"):
                slot["cls"] = str(el.get("cls"))[:60]

    scr["elements"] = list(known.values())[-_MAX_ELEMENTS_PER_SCREEN:]
    scr["last_seen"] = now
    scr["surveys"] = int(scr.get("surveys") or 0) + 1
    record_app(data, app, name)
    return {"screen_key": key, "added": added, "updated": updated, "total": len(scr["elements"])}


def record_exit(data: dict, from_key: str, label: str, to_key: str) -> None:
    """Remember: tapping `label` on screen `from_key` opened screen `to_key`."""
    if not (from_key and label and to_key) or from_key == to_key:
        return
    scr = (data.get("screens") or {}).get(from_key)
    if not isinstance(scr, dict):
        return
    exits = scr.setdefault("exits", {})
    exits[str(label)[:80]] = str(to_key)
    if len(exits) > _MAX_EXITS_PER_SCREEN:
        for stale in list(exits)[: len(exits) - _MAX_EXITS_PER_SCREEN]:
            exits.pop(stale, None)


def note_function(
    data: dict,
    goal: str,
    app: str = "",
    screen: str = "",
    steps=None,
    notes: str = "",
    source: str = "",
) -> str:
    """Save a named path/function: «что сделать» → шаги. Returns its key."""
    goal = (goal or "").strip()
    if not goal:
        return ""
    key = re.sub(r"\s+", " ", goal.lower())[:120]
    funcs = data.setdefault("functions", {})
    prev = funcs.get(key) if isinstance(funcs.get(key), dict) else {}
    if isinstance(steps, str):
        steps = re.split(r"\s*(?:;|->|→|\n)\s*", steps)
    clean_steps = [str(s).strip()[:120] for s in (steps or []) if str(s).strip()][:12]
    funcs[key] = {
        "goal": goal[:160],
        "app": ((app or "").strip() or prev.get("app", ""))[:120],
        "screen": ((screen or "").strip() or prev.get("screen", ""))[:160],
        "steps": clean_steps or list(prev.get("steps") or [])[:12],
        "notes": (notes or "").strip()[:400] or prev.get("notes", ""),
        "source": source or prev.get("source", ""),
        "updated": _stamp(),
    }
    return key


def _fold(text: str) -> str:
    """Сравнение без регистра, дефисов и пробелов: «Wi-Fi» == «wifi»."""
    return re.sub(r"[^0-9a-zа-яё]+", "", (text or "").lower().replace("ё", "е"))


def _score(text: str, query: str, words: list[str]) -> float:
    low = (text or "").lower()
    if not low:
        return 0.0
    folded = _fold(text)
    score = 0.0
    if query:
        if query in low:
            score += 3.0
        elif _fold(query) and _fold(query) in folded:
            score += 3.0
    for word in words:
        if word in low:
            score += 0.8
        elif _fold(word) and _fold(word) in folded:
            score += 0.8
    return score


def search(data: dict, query: str, limit: int = 8) -> list[dict]:
    """Best matches across functions, screen elements and app names."""
    query = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not query:
        return []
    words = [w for w in query.split() if len(w) > 1] or [query]
    hits: list[dict] = []

    for fn in (data.get("functions") or {}).values():
        if not isinstance(fn, dict):
            continue
        goal = fn.get("goal", "")
        haystack = " ".join(
            [goal, fn.get("notes", ""), fn.get("app", ""), " ".join(fn.get("steps") or [])]
        )
        base = _score(haystack, query, words)
        if base <= 0:
            continue
        hits.append(
            {
                "kind": "function",
                "score": base + 1.0 + 0.5 * _score(goal, query, words),
                "goal": goal,
                "app": fn.get("app", ""),
                "screen": fn.get("screen", ""),
                "steps": fn.get("steps") or [],
                "notes": fn.get("notes", ""),
                "updated": fn.get("updated", ""),
            }
        )

    for key, scr in (data.get("screens") or {}).items():
        if not isinstance(scr, dict):
            continue
        label = scr.get("label", "")
        for el in scr.get("elements") or []:
            if not isinstance(el, dict):
                continue
            el_label = el.get("label") or el.get("id") or ""
            score = _score(el_label, query, words)
            if score > 0:
                hits.append(
                    {
                        "kind": "element",
                        "score": score + (0.4 if el.get("clickable") else 0.0),
                        "label": el_label,
                        "screen": label or key,
                        "app": scr.get("app", ""),
                        "clickable": el.get("clickable"),
                        "seen": el.get("seen", ""),
                    }
                )
        for tap_label, dest in (scr.get("exits") or {}).items():
            score = _score(tap_label, query, words)
            if score > 0:
                hits.append(
                    {
                        "kind": "route",
                        "score": score + 0.3,
                        "label": tap_label,
                        "screen": label or key,
                        "app": scr.get("app", ""),
                        "dest": dest,
                    }
                )

    for package, app in (data.get("apps") or {}).items():
        if not isinstance(app, dict):
            continue
        score = 0.9 * (_score(app.get("label", ""), query, words) + _score(package, query, words))
        if score > 0:
            hits.append(
                {
                    "kind": "app",
                    "score": score,
                    "label": app.get("label") or package,
                    "package": package,
                }
            )

    hits.sort(key=lambda h: -h.get("score", 0.0))
    out: list[dict] = []
    seen: set[tuple] = set()
    for hit in hits:
        dedup_key = (hit.get("kind"), hit.get("label", ""), hit.get("screen", ""))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        out.append(hit)
        if len(out) >= limit:
            break
    return out


def overview(data: dict) -> str:
    """Human-readable summary of what is already known about the device."""
    dev = data.get("device") or {}
    apps = data.get("apps") or {}
    screens = data.get("screens") or {}
    funcs = data.get("functions") or {}
    elements = sum(
        len(s.get("elements") or []) for s in screens.values() if isinstance(s, dict)
    )
    name = dev.get("name") or dev.get("model") or "Android"
    details = []
    if dev.get("android"):
        details.append(f"Android {dev['android']}")
    if dev.get("screen"):
        details.append(f"экран {dev['screen']}")
    header = f"Карта устройства {name}" + (f" ({', '.join(details)})" if details else "")
    lines = [
        header + ".",
        f"Изучено: экранов {len(screens)} (элементов {elements}), приложений {len(apps)}, "
        f"знаний-путей {len(funcs)}. Последний обзор: {dev.get('last_seen', '—')}.",
    ]
    if funcs:
        lines.append("Пути и функции:")
        for fn in sorted(funcs.values(), key=lambda f: f.get("updated", ""), reverse=True)[:6]:
            steps = " → ".join(fn.get("steps") or []) or "шаги не записаны"
            lines.append(f"• «{fn.get('goal')}»: {steps} ({fn.get('updated', '')})")
    if screens:
        lines.append("Экраны:")
        for scr in sorted(screens.values(), key=lambda s: s.get("last_seen", ""), reverse=True)[:10]:
            if not isinstance(scr, dict):
                continue
            label = scr.get("label") or scr.get("activity") or scr.get("app") or "?"
            lines.append(
                f"• {label} — {len(scr.get('elements') or [])} элементов, "
                f"переходов {len(scr.get('exits') or {})}"
            )
    lines.append(
        "Подсказка: recall_place «запрос» — найти в карте; tap_element (text=…) — нажать по подписи; "
        "survey_screen / survey_device — дополнить карту."
    )
    return "\n".join(lines)


def prune(data: dict) -> None:
    """Drop the oldest entries so a long-lived map stays bounded."""
    screens = data.get("screens") or {}
    if len(screens) > _MAX_SCREENS:
        oldest = sorted(screens, key=lambda k: screens[k].get("last_seen", ""))
        for key in oldest[: len(screens) - _MAX_SCREENS]:
            screens.pop(key, None)
    funcs = data.get("functions") or {}
    if len(funcs) > _MAX_FUNCTIONS:
        oldest = sorted(funcs, key=lambda k: funcs[k].get("updated", ""))
        for key in oldest[: len(funcs) - _MAX_FUNCTIONS]:
            funcs.pop(key, None)
    apps = data.get("apps") or {}
    if len(apps) > _MAX_APPS:
        oldest = sorted(apps, key=lambda k: apps[k].get("last_seen", ""))
        for key in oldest[: len(apps) - _MAX_APPS]:
            apps.pop(key, None)
