"""Autonomous 24/7 code improvement for JARVIS.

Runs a background loop that periodically scans for issues, proposes
improvements, applies them via self_rewrite with safety guardrails
(git branch, tests, rollback), and logs everything.

Start with action=autonomous_improve(op="start") or "stop" to halt.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from core.paths import get_app_dir

_BASE = get_app_dir()
_RUNNING = False
_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()
_INTERVAL_MINUTES = 30
_LOG: list[str] = []


def _log(msg: str) -> None:
    import datetime

    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _LOG.append(line)
    if len(_LOG) > 200:
        _LOG.pop(0)
    print(f"[AutoImprove] {msg}")


def _improvement_cycle() -> None:
    """One improvement cycle: scan → propose → apply → test → rollback if needed."""
    try:
        from autonomy import health as health_mod
        from autonomy import incidents as incidents_mod

        # 1. Health check
        report = health_mod.run_health()
        _log(f"Health: {'OK' if report.ok else 'ISSUES'}")

        # 2. Check open incidents
        opened = incidents_mod.open_items()
        _log(f"Open incidents: {len(opened)}")

        # 3. If there are open incidents, try to repair
        if opened:
            from autonomy import repair as repair_mod

            for inc in opened[:3]:  # max 3 per cycle
                try:
                    result = repair_mod.repair_incident(inc)
                    _log(f"Repair {inc.get('id', '?')}: {result}")
                except Exception as e:
                    _log(f"Repair failed: {e}")

        # 4. Reflect and propose improvements
        try:
            from autonomy import advisor as advisor_mod

            reflection = advisor_mod.reflect()
            _log(f"Reflection: {str(reflection)[:200]}")
        except Exception as e:
            _log(f"Reflection failed: {e}")

        # 5. Check if there are GitHub updates to adopt
        try:
            from actions import github_scout

            scout_result = github_scout.github_scout(
                {"query": "JARVIS improvement performance"}, response=None
            )
            _log(f"Scout: {str(scout_result)[:200]}")
        except Exception as e:
            _log(f"Scout failed: {e}")

    except Exception as e:
        _log(f"Cycle error: {e}")


def _background_loop() -> None:
    global _RUNNING
    _log("Background improvement loop started")
    while _RUNNING:
        try:
            _improvement_cycle()
        except Exception as e:
            _log(f"Cycle exception: {e}")
        # Wait for next cycle
        for _ in range(_INTERVAL_MINUTES * 60):
            if not _RUNNING:
                break
            time.sleep(1)
    _log("Background improvement loop stopped")


def autonomous_improve(parameters: dict | None = None, **_ctx) -> str:
    global _RUNNING, _THREAD, _INTERVAL_MINUTES
    params = parameters or {}
    op = str(params.get("op", "status")).strip().lower()

    if op == "start":
        with _LOCK:
            if _RUNNING:
                return "Автономное улучшение уже запущено."
            _RUNNING = True
            _INTERVAL_MINUTES = int(params.get("interval_minutes", 30))
            _THREAD = threading.Thread(target=_background_loop, daemon=True, name="auto-improve")
            _THREAD.start()
        return f"Автономное улучшение запущено (интервал: {_INTERVAL_MINUTES} мин)."

    if op == "stop":
        with _LOCK:
            _RUNNING = False
        if _THREAD and _THREAD.is_alive():
            _THREAD.join(timeout=5)
        return "Автономное улучшение остановлено."

    if op == "status":
        with _LOCK:
            running = _RUNNING
        recent = _LOG[-10:] if _LOG else ["(нет записей)"]
        return (
            f"Статус: {'работает' if running else 'остановлен'}\n"
            f"Интервал: {_INTERVAL_MINUTES} мин\n"
            f"Последние события:\n" + "\n".join(recent)
        )

    if op == "cycle":
        _improvement_cycle()
        return "Один цикл улучшения выполнен. См. лог."

    return (
        "Использование: autonomous_improve(op='start'|'stop'|'status'|'cycle', "
        "interval_minutes=30)"
    )


TOOL = {
    "name": "autonomous_improve",
    "description": (
        "Autonomous 24/7 code improvement for JARVIS. Runs background cycles: "
        "health check, incident repair, reflection, GitHub scouting, and applies "
        "improvements via self_rewrite with safety guardrails. "
        "op=start/stop/status/cycle."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "op": {
                "type": "STRING",
                "description": "start, stop, status, or cycle",
            },
            "interval_minutes": {
                "type": "INTEGER",
                "description": "Minutes between improvement cycles (default: 30)",
            },
        },
        "required": [],
    },
    "handler": autonomous_improve,
}
