"""Фоновый цикл обслуживания: работает сам, без команд — постоянно.

Ритм (тихие демон-потоки, ассистенту не мешают):
  * каждые 5 минут — осмотр: новые падения в логе → чинить;
  * каждые 30 минут — уборка и замер ресурсов;
  * каждые 10 минут — разведка GitHub и встройка одного прошедшего проверки инструмента.
Все ошибки глушатся: цикл не имеет права ронять ассистента.
"""
from __future__ import annotations

import threading
import time

from . import journal as journal_mod

_thread: threading.Thread | None = None
_stop = threading.Event()

DEFAULT_QUERY = "llm agents tools language:python stars:>100"

REPAIR_EVERY = 5 * 60          # осмотр и починка
OPTIMIZE_EVERY = 30 * 60      # уборка и замеры
SCOUT_EVERY = 10 * 60         # разведка и встройка GitHub
FIRST_DELAY = 2 * 60          # первый осмотр через 2 минуты после старта


def settings() -> dict:
    s = {"enabled": True, "auto_repair": True,
         "repair_every": REPAIR_EVERY, "optimize_every": OPTIMIZE_EVERY,
         "scout_every": SCOUT_EVERY,
         "do_scout": True, "do_optimize": True, "query": DEFAULT_QUERY,
         "idle_only": True, "idle_after": 300.0}
    try:
        from memory.config_manager import load_api_keys

        data = load_api_keys()
        s["enabled"] = bool(data.get("autonomy_enabled", True))
        s["auto_repair"] = bool(data.get("autonomy_auto_repair", True))
        for key, low in (("autonomy_repair_every", 60),
                         ("autonomy_optimize_every", 300),
                         ("autonomy_scout_every", 600)):
            try:
                val = float(data.get(key, 0) or 0)
                if val >= low:
                    s[{"autonomy_repair_every": "repair_every",
                       "autonomy_optimize_every": "optimize_every",
                       "autonomy_scout_every": "scout_every"}[key]] = val
            except (TypeError, ValueError):
                pass
        # Старый интервал применяется только когда новый ключ не задан.
        try:
            old = float(data.get("autonomy_cycle_hours", 0) or 0)
            if not data.get("autonomy_scout_every") and old >= 1:
                s["scout_every"] = old * 3600.0
        except (TypeError, ValueError):
            pass
        s["do_scout"] = bool(data.get("autonomy_scout", True))
        s["do_optimize"] = bool(data.get("autonomy_optimize", True))
        s["idle_only"] = bool(data.get("autonomy_idle_only", True))
        try:
            idle_after = float(data.get("autonomy_idle_after_seconds", 300) or 300)
            if 60 <= idle_after <= 3600:
                s["idle_after"] = idle_after
        except (TypeError, ValueError):
            pass
        s["query"] = str(data.get("autonomy_scout_query", DEFAULT_QUERY))
    except Exception:
        pass
    return s


def run_repair() -> dict:
    """Осмотр и починка (дешёвый проход, интервал задаётся в настройках)."""
    from . import incidents as incidents_mod
    from . import repair as repair_mod

    cfg = settings()
    res = {"fixed": 0, "failed": 0}
    if not cfg["auto_repair"]:
        return res
    for r in repair_mod.repair_open(limit=3):
        if r.get("ok"):
            res["fixed"] += 1
        else:
            res["failed"] += 1
    res["open"] = len(incidents_mod.open_items())
    return res


def run_scout() -> list[str]:
    """Найти кандидатов и попросить автовстройщик выбрать подходящую возможность."""
    from . import scout as scout_mod

    cfg = settings()
    try:
        cands = scout_mod.search(cfg["query"], limit=5)
    except Exception as e:  # noqa: BLE001
        journal_mod.add("note", f"разведка GitHub не вышла: {e}"[:150])
        return []
    names = [c["full_name"] for c in cands[:5]]
    if cands:
        top = cands[0]
        journal_mod.add(
            "note",
            f"разведал GitHub: топ — {top['full_name']} "
            f"(звёзд {top['stars']}, {top['language']}) — "
            f"{top['description'][:100]}")
    try:
        scout_mod.auto_adopt(cands)  # успех и отказ записываются самим автовстройщиком
    except scout_mod.RateLimited as e:
        when = ""
        if e.reset_ts:
            from datetime import datetime as _dt

            when = _dt.fromtimestamp(e.reset_ts).strftime(", квота вернётся в %H:%M")
        journal_mod.add("note", f"автовстройка ждёт квоту GitHub{when}")
    except Exception as e:  # noqa: BLE001 — ошибки интеграции не роняют цикл
        journal_mod.add("note", f"автовстройка не вышла: {e}"[:150])
    return names


def run_optimize() -> str:
    """Уборка и замер (средний проход)."""
    from . import optimizer as optimizer_mod

    try:
        tidied = optimizer_mod.tidy()
        text = optimizer_mod.report()
        journal_mod.add(
            "note", f"прибрался: вычистил кэша {tidied['pycache_dirs']} папок")
        return text
    except Exception as e:  # noqa: BLE001
        journal_mod.add("note", f"уборка не вышла: {e}"[:150])
        return ""


def run_once() -> dict:
    """Один полный цикл сразу (по голосовой команде)."""
    from . import incidents as incidents_mod

    cfg = settings()
    # A user-requested manual cycle is intentionally not gated by idle time.
    parts: dict = {"fixed": 0, "failed": 0, "scout": [], "optimize": ""}
    rep = run_repair()
    parts.update({k: rep.get(k, 0) for k in ("fixed", "failed")})
    if cfg["do_scout"]:
        parts["scout"] = run_scout()
    if cfg["do_optimize"]:
        parts["optimize"] = run_optimize()
    opened = len(incidents_mod.open_items())
    summary = (f"фоновый цикл: починил {parts['fixed']}, не вышло {parts['failed']}, "
               f"открытых осталось {opened}")
    if parts["scout"]:
        summary += f", разведал: {', '.join(parts['scout'][:3])}"
    journal_mod.add("cycle", summary)
    parts["summary"] = summary
    return parts


def run_idle_pass() -> dict:
    """One bounded maintenance pass; network discovery is optional and read-only."""
    from . import incidents as incidents_mod

    cfg = settings()
    rep = run_repair()
    optimize = run_optimize() if cfg["do_optimize"] else ""
    names = run_idle_scout() if cfg["do_scout"] else []
    summary = (
        f"режим простоя: исправлено {rep.get('fixed', 0)}, "
        f"не вышло {rep.get('failed', 0)}, открытых ошибок "
        f"{len(incidents_mod.open_items())}; "
        f"GitHub-находки без установки: {', '.join(names[:3]) if names else 'нет'}"
    )
    journal_mod.add("cycle", summary)
    return {"fixed": rep.get("fixed", 0), "failed": rep.get("failed", 0),
            "optimize": optimize, "scout": names, "summary": summary}


def run_idle_scout() -> list[str]:
    """Search public metadata only and store findings; do not fetch source code."""
    from . import scout as scout_mod

    cfg = settings()
    try:
        candidates = scout_mod.search(cfg["query"], limit=5)
    except Exception as e:
        journal_mod.add("note", f"разведка GitHub не вышла: {e}"[:150])
        return []
    names = [item.get("full_name", "") for item in candidates[:5]
             if item.get("full_name")]
    if candidates:
        top = candidates[0]
        journal_mod.add(
            "note",
            f"idle-разведка GitHub (без загрузки исходников): топ — "
            f"{top.get('full_name', '?')} (звёзд {top.get('stars', 0)}, "
            f"{top.get('language', '?')}) — {top.get('description', '')[:100]}",
        )
    return names


def _loop() -> None:
    last_repair = 0.0
    last_optimize = 0.0
    last_scout = 0.0
    _stop.wait(FIRST_DELAY)
    while not _stop.is_set():
        try:
            if _stop.is_set():
                break
            cfg = settings()
            now = time.time()
            if not cfg["enabled"]:
                _stop.wait(300.0)
                continue
            if cfg["idle_only"]:
                from .idle import idle_seconds

                inactive = idle_seconds()
                # Do not mistake a locked/sleeping/unsupported Windows session
                # for owner-approved idle time. The loop stays read-only then.
                if inactive is None or inactive < cfg["idle_after"]:
                    _stop.wait(60.0)
                    continue
            due_repair = now - last_repair >= cfg["repair_every"]
            due_optimize = cfg["do_optimize"] and now - last_optimize >= cfg["optimize_every"]
            due_scout = cfg["do_scout"] and now - last_scout >= cfg["scout_every"]
            if due_repair or due_optimize or due_scout:
                # Run one bounded idle-only pass, then recheck activity before
                # any next work. This prevents loops of full-project scans.
                run_idle_pass()
                last_repair = last_optimize = last_scout = time.time()
            _stop.wait(60.0 if cfg["idle_only"] else 300.0)
        except Exception as e:
            # A persistent failure must not turn the daemon into a hot loop.
            try:
                journal_mod.add("note", f"цикл автономности приостановил проход: {e}"[:180])
            except Exception:
                pass
            _stop.wait(30.0)


def start() -> bool:
    """Запустить фоновый цикл (идемпотентно)."""
    global _thread
    if _thread and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="jarvis-autonomy-cycle",
                               daemon=True)
    _thread.start()
    return True
