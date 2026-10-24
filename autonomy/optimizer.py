"""Самооптимизация: метрики, чистка, базовая линия, отчёт по-русски.

Безопасные действия без спроса: чистка __pycache__, старых снапшотов сверх
лимита, пустых временных файлов. Всё остальное — только отчёт владельцу.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import BASE_DIR, STATE_DIR
from . import journal as journal_mod

METRICS_FILE = STATE_DIR / "metrics.jsonl"
BASELINE_FILE = STATE_DIR / "baseline.json"


def _rss_mb() -> float:
    """Память текущего процесса, МБ (через tasklist — надёжно на Windows)."""
    import os
    import re
    import subprocess

    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {os.getpid()}", "/NH", "/FO", "CSV"],
            capture_output=True, timeout=10,
        ).stdout.decode("cp866", "replace")
        m = re.search(r'"([\d\s\xa0.,]+)\s*(?:[Kk]|К)', out)
        if m:
            num = re.sub(r"[^\d]", "", m.group(1))
            return round(int(num) / 1024, 1) if num else 0.0
    except Exception:
        pass
    return 0.0


def _du_mb(path: Path) -> float:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return round(total / 1024 / 1024, 1)


def collect() -> dict:
    """Снять метрики и сохранить. Возвращает сводку."""
    snap = {"ts": time.time(), "rss_mb": _rss_mb(), "pycache_mb": 0.0,
            "logs_mb": _du_mb(BASE_DIR / "logs")}
    pyc_total = 0.0
    for sub in ("actions", "plugins", "dashboard", "autonomy", "core", "memory"):
        pyc_total += _du_mb(BASE_DIR / sub / "__pycache__")
    snap["pycache_mb"] = round(pyc_total, 1)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(METRICS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(snap) + "\n")
    return snap


def tidy() -> dict:
    """Безопасная уборка: кэш байткода и старые снапшоты сверх лимита."""
    removed_pyc = 0
    for sub in ("actions", "plugins", "dashboard", "autonomy", "core", "memory",
                "mcp_servers"):
        for p in (BASE_DIR / sub).rglob("__pycache__"):
            try:
                shutil.rmtree(p, ignore_errors=True)
                removed_pyc += 1
            except OSError:
                pass
    from . import snapshots as snapshots_mod

    before = len(snapshots_mod.list_snaps(limit=1000))
    snapshots_mod._gc()
    after = len(snapshots_mod.list_snaps(limit=1000))
    return {"pycache_dirs": removed_pyc, "snapshots_dropped": before - after}


def report() -> str:
    snap = collect()
    day = datetime.now().strftime("%Y-%m-%d")
    base = {}
    if BASELINE_FILE.exists():
        try:
            base = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        except ValueError:
            base = {}
    if not base or base.get("day") != day:
        baseline = {**snap, "day": day}
        BASELINE_FILE.write_text(json.dumps(baseline), encoding="utf-8")
        msg = (f"Замерил базу: память процесса {snap['rss_mb']} МБ, "
               f"логи {snap['logs_mb']} МБ, кэш {snap['pycache_mb']} МБ.")
        journal_mod.add("perf", msg)
        return msg + " Сравнение начнётся с этого запуска."
    lines = [f"Память процесса: {snap['rss_mb']} МБ (было {base.get('rss_mb')}).",
             f"Логи: {snap['logs_mb']} МБ. Кэш байткода: {snap['pycache_mb']} МБ."]
    if snap["rss_mb"] and base.get("rss_mb"):
        diff = round((snap["rss_mb"] - base["rss_mb"]) / base["rss_mb"] * 100, 1)
        if diff <= -10:
            lines.append(f"Стал легче на {abs(diff)}% — хороший день.")
        elif diff >= 20:
            lines.append(f"Потолстел на {diff}% — стоит посмотреть, что растёт.")
        else:
            lines.append("Вес стабильный.")
    msg = " ".join(lines)
    journal_mod.add("perf", msg)
    return msg
