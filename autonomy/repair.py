"""Цикл починки: снапшот → патч от модели → проверки → откат при провале."""
from __future__ import annotations

import ast
import json
import threading
from datetime import datetime

from . import BASE_DIR, STATE_DIR, is_editable
from . import incidents as incidents_mod
from . import journal as journal_mod
from . import snapshots as snapshots_mod
from .health import run_health

MAX_FILE_CHARS = 60000
MAX_REPAIRS_PER_DAY = 10
_REPAIR_LOCK = threading.RLock()


def _repairs_today() -> int:
    try:
        data = json.loads((STATE_DIR / "repairs_today.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return int(data.get(datetime.now().strftime("%Y-%m-%d"), 0))


def _bump_repairs() -> None:
    path = STATE_DIR / "repairs_today.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    day = datetime.now().strftime("%Y-%m-%d")
    data[day] = int(data.get(day, 0)) + 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(rel: str) -> str:
    path = (BASE_DIR / rel).resolve()
    if not path.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("путь выходит за пределы проекта")
    return path.read_text(encoding="utf-8")


def _write(rel: str, text: str) -> None:
    path = (BASE_DIR / rel).resolve()
    if not path.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("путь выходит за пределы проекта")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".jarvis-repair.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _validate_generated(rel: str, new_rel: str, new_text: str) -> str | None:
    """Проверить целевой путь и базовую полноту ответа модели."""
    normalized = (new_rel or "").replace("\\", "/").strip()
    if normalized != rel:
        return f"модель изменила целевой путь: {normalized or '(пусто)'}"
    if not isinstance(new_text, str) or not new_text.strip():
        return "модель вернула пустой файл"
    if len(new_text) > MAX_FILE_CHARS * 2:
        return "ответ модели слишком большой"
    return None


def _public_symbols(source: str) -> list[str]:
    """Keep the module's existing public entry points across an AI rewrite."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "TOOL":
                    names.add("TOOL")
    return sorted(names)


def _repair_incident(inc: dict, daily_limit: int = 10) -> dict:
    """Одна попытка цикла по инциденту. Ошибка никогда не оставляет полупатч."""
    from . import generator as gen_mod

    rel = (inc.get("where") or "").replace("\\", "/").strip()
    if not rel or not is_editable(rel):
        incidents_mod.update(inc["id"], status="ignored",
                             note="вне разрешённой зоны автономности")
        journal_mod.add("note", f"пропустил {inc.get('error', '')[:100]} — вне моей зоны")
        return {"ok": False, "summary": "вне зоны правок"}
    attempts = int(inc.get("attempts", 0))
    if attempts >= incidents_mod.MAX_ATTEMPTS:
        incidents_mod.update(inc["id"], status="failed",
                             note="исчерпан лимит попыток для инцидента")
        return {"ok": False, "summary": "исчерпан лимит попыток для инцидента"}
    if _repairs_today() >= min(daily_limit, MAX_REPAIRS_PER_DAY):
        return {"ok": False, "summary": "дневной лимит автопочинок исчерпан"}
    try:
        content = _read(rel)
    except (OSError, ValueError) as e:
        incidents_mod.update(inc["id"], status="failed", note=f"файл не читается: {e}")
        return {"ok": False, "summary": "файл не читается"}
    if len(content) > MAX_FILE_CHARS:
        incidents_mod.update(inc["id"], status="failed", note="файл слишком большой")
        return {"ok": False, "summary": "файл слишком большой"}
    required_symbols = _public_symbols(content)

    sid = snapshots_mod.snapshot([rel], "fix", f"repair {inc['id']}")
    _bump_repairs()
    feedback = ""
    while attempts < incidents_mod.MAX_ATTEMPTS:
        attempts += 1
        incidents_mod.update(inc["id"], attempts=attempts)
        accepted = False
        try:
            new_rel, new_text = gen_mod.generate_fix(
                rel, content, inc.get("error", ""), inc.get("tb", ""), feedback)
            invalid = _validate_generated(rel, new_rel, new_text)
            if invalid:
                feedback = invalid + "; верни полный файл по исходному пути"
                continue
            if len(new_text) < len(content) * 0.5:
                feedback = "новый файл подозрительно короткий — верни полный файл"
                continue

            _write(rel, new_text)
            rep = run_health(changed_rel=rel, functional=True,
                             required_symbols=required_symbols)
            if rep.ok:
                incidents_mod.update(inc["id"], status="fixed", attempts=attempts,
                                     note=f"починено за {attempts} попыток (снапшот {sid})")
                journal_mod.add("fix", f"починил {inc.get('error', '')[:120]} в {rel} "
                                       f"(попыток: {attempts}, проверки прошли)")
                accepted = True
                return {"ok": True, "summary": f"починено за {attempts} попыток",
                        "snapshot": sid}
            feedback = rep.text()[-1500:]
        except Exception as e:  # noqa: BLE001 — любая ошибка должна приводить к откату
            feedback = f"ошибка при проверке патча: {e}"[:1000]
        finally:
            # После неуспешной проверки/записи исходник нужен и модели, и следующей попытке.
            try:
                if not accepted and _read(rel) != content:
                    snapshots_mod.rollback(sid)
            except OSError:
                snapshots_mod.rollback(sid)

    incidents_mod.update(inc["id"], status="failed",
                         note=f"{attempts} попыток, откат выполнен")
    journal_mod.add("fail", f"не смог починить {inc.get('error', '')[:120]} в {rel} "
                            f"за {attempts} попыток — откатил как было")
    return {"ok": False, "summary": "не вышло, откатил как было", "rolled_back": True}


def repair_incident(inc: dict, daily_limit: int = 10) -> dict:
    """Serialize repairs so concurrent workers cannot overwrite one another."""
    with _REPAIR_LOCK:
        return _repair_incident(inc, daily_limit=daily_limit)


def repair_open(limit: int = 5) -> list[dict]:
    out = []
    for inc in incidents_mod.open_items()[:limit]:
        try:
            res = repair_incident(inc)
        except Exception as e:  # noqa: BLE001 — починка не должна ронять ассистента
            try:
                snapshots_mod.rollback_for_incident(inc["id"])
            except Exception:
                pass
            incidents_mod.update(inc["id"], status="failed", note=f"ошибка починки: {e}"[:200])
            res = {"ok": False, "summary": f"внутренняя ошибка починки: {e}"}
        out.append({"id": inc["id"], **res})
    return out
