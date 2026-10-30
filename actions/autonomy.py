"""Голосовое управление автономностью: статус, починка, GitHub, дневник.

Примеры:
    «как твоё здоровье» / «доложи о себе» → autonomy(op="status")
    «починись» / «почини ошибки»           → autonomy(op="repair")
    «найди на гитхабе ...»                 → autonomy(op="scout", query="...")
    «что нового» / «что ты сегодня делал»  → autonomy(op="journal")
    «ускорься» / «оптимизируйся»           → autonomy(op="optimize")
    «как тебе стать лучше» / «что бы ты улучшил» → autonomy(op="reflect")

Ручная встройка с GitHub (adopt) — по явной просьбе с точным owner/repo.
Фоновый цикл может сам встроить до 144 прошедших проверки инструментов в день
(один за цикл, с учётом квот поставщиков).

Если пользователь прислал ссылку на GitHub как возможное улучшение JARVIS,
используй op=integrate: сравни проект с кодом JARVIS и внеси только полезный,
ограниченный изменениями патч, прошедший ревью и проверки.
"""
from __future__ import annotations


def autonomy(parameters=None, **_ctx) -> str:
    from autonomy import health as health_mod
    from autonomy import incidents as incidents_mod
    from autonomy import journal as journal_mod
    from autonomy import optimizer as optimizer_mod
    from autonomy import repair as repair_mod
    from autonomy import scout as scout_mod

    p = parameters or {}
    op = str(p.get("op", "status")).strip().lower()

    if op == "models":
        from core import gemini

        return gemini.describe_routes()

    if op == "reflect":
        from autonomy import advisor as advisor_mod

        return advisor_mod.reflect()

    if op == "status":
        from datetime import datetime
        from autonomy import cycle as cycle_mod

        rep = health_mod.run_health()
        opened = incidents_mod.open_items()
        recent_incidents = incidents_mod.all_items(50)
        failed = [item for item in recent_incidents
                  if item.get("status") == "failed"]
        parts = [rep.text()]
        if opened:
            parts.append(f"Открытых инцидентов: {len(opened)}.")
        else:
            parts.append("Открытых инцидентов нет.")
        if failed:
            parts.append(
                f"В последних 50 записях есть {len(failed)} неудачных попыток "
                "автопочинки; это исторические записи, и сами по себе они не "
                "подтверждают текущую неисправность."
            )
            for item in failed[-5:]:
                note = str(item.get("note") or "")[:120]
                parts.append(
                    f"{item.get('where', 'неизвестный модуль')}: "
                    f"{item.get('error', '')[:180]}"
                    + (f" (примечание: {note})" if note else "")
                )
        today_key = datetime.now().strftime("%Y-%m-%d")
        today = [e for e in journal_mod.recent_entries(2000)
                 if e.get("day") == today_key]
        fixes = sum(1 for e in today if e.get("kind") == "fix")
        feats = sum(1 for e in today if e.get("kind") == "feature")
        parts.append(f"Сегодня: починок {fixes}, новинок с GitHub {feats}.")
        try:
            from memory import lessons as lessons_mod

            summary = lessons_mod.stats()
            parts.append(
                f"Уроков на ошибках: {summary['total']}, "
                f"открытых сбоев: {summary['open']}, "
                f"правил владельца: {summary['owner']}."
            )
        except Exception:
            pass
        cfg = cycle_mod.settings()
        parts.append(
            "Фоновое развитие "
            + ("включено" if cfg["enabled"] else "выключено")
            + f", самопочинка {'включена' if cfg['auto_repair'] else 'выключена'}, "
            + f"GitHub-поиск каждые {int(cfg['scout_every'] // 60)} мин., "
            + (f"запускается после {int(cfg['idle_after'] // 60)} мин. простоя ПК."
               if cfg.get("idle_only") else "не привязано к простою ПК.")
        )
        return " ".join(parts)

    if op == "idle":
        from autonomy import cycle as cycle_mod

        cfg = cycle_mod.settings()
        parts = [
            "Режим простоя включён." if cfg.get("idle_only") else "Режим простоя выключен.",
            f"Порог: {int(cfg.get('idle_after', 300) // 60)} минут без ввода.",
            "В фоне разрешены проверка ошибок, ограниченная автопочинка, уборка и поиск GitHub без установки.",
            "Автовстройка стороннего кода отключена.",
        ]
        try:
            from autonomy.idle import idle_seconds

            elapsed = idle_seconds()
            parts.append(
                f"Сейчас без ввода {int(elapsed // 60)} мин."
                if elapsed is not None else "Windows не предоставила состояние простоя."
            )
        except Exception:
            parts.append("Не удалось прочитать состояние простоя Windows.")
        return " ".join(parts)

    if op == "repair":
        opened = incidents_mod.open_items()
        if not opened:
            return "Ломаться нечему — открытых инцидентов нет."
        results = repair_mod.repair_open(limit=3)
        out = []
        for r in results:
            out.append(("Починил: " if r.get("ok") else "Не вышло: ") + r.get("summary", ""))
        return " ".join(out)

    if op == "scout":
        query = str(p.get("query", "") or "").strip()
        cands = scout_mod.search(query or "llm agents tools language:python stars:>100")
        if not cands:
            return "На GitHub ничего подходящего не нашлось (или квота API)."
        lines = []
        for c in cands[:5]:
            lines.append(f"{c['full_name']} (звёзд {c['stars']}): {c['description']}")
        names = "; ".join(n["full_name"] for n in cands[:5])
        journal_mod.add("note", f"разведал GitHub: {names}")
        return ("Нашёл: " + " … ".join(lines)
                + ". Встроить что-то? Назови точное имя, например «встрой owner/repo».")

    if op == "adopt":
        repo = str(p.get("repo", "") or "").strip()
        if not repo or "/" not in repo:
            return "Какое встроить? Нужно точное имя вида owner/repo."
        info = scout_mod.summarize_repo(repo)
        res = scout_mod.adopt(repo)
        if res.get("ok"):
            return f"Встроил {repo}. Разбор: {info[:400]}"
        return f"Не стал встраивать {repo}: {res.get('error')}"

    if op == "integrate":
        from autonomy import integrator as integrator_mod

        repo = str(p.get("repo", "") or "").strip()
        if not repo:
            return "Пришли ссылку GitHub или имя owner/repo."
        result = integrator_mod.integrate(repo)
        return str(result.get("summary") or "Интеграция не вернула результат.")

    if op == "journal":
        entries = journal_mod.recent_entries(8)
        if not entries:
            return "Дневник пока пуст — день только начался."
        kinds = {"fix": "починил", "fail": "не вышло", "feature": "добавил",
                 "perf": "скорость", "note": "заметка", "health": "проверка",
                 "cycle": "цикл"}
        return " ".join(f"{kinds.get(e.get('kind'), 'запись')}: {e.get('text','')[:150]}"
                        for e in entries[-5:])

    if op == "optimize":
        tidied = optimizer_mod.tidy()
        rep = optimizer_mod.report()
        return (f"Прибрался: кэша {tidied['pycache_dirs']} папок. {rep}")

    if op == "cycle":
        from autonomy import cycle as cycle_mod

        res = cycle_mod.run_idle_pass()
        return res.get("summary", "Цикл прошёл.")

    raise RuntimeError("op должен быть: status, idle, models, reflect, repair, scout, adopt, integrate, journal, optimize, cycle.")


TOOL = {
    "name": "autonomy",
    "description": (
        "Самообслуживание JARVIS: здоровье, починка своих ошибок, разведка GitHub, "
        "фоновый поиск может встроить до 144 проверенных инструментов в день; "
        "дневник развития, оптимизация. «доложи о себе»/«как здоровье» → status; "
        "«дай совет по самоулучшению»/«что бы ты улучшил» → reflect: "
        "анализирует ограниченный снимок исходников, маршруты моделей, настройки, "
        "инциденты, дневник и метрики; возвращает ранжированные советы с файлами-основаниями, "
        "первым шагом, трудоёмкостью и рисками. Только совет: не меняет файлы, не запускает проверки; "
        "«починись» → repair; «какие модели используешь» → models; "
        "«найди на гитхабе X» → scout (query=X); "
        "«встрой owner/repo» → adopt (только по явной просьбе с точным именем); "
        "«сверь и улучши по ссылке GitHub» → integrate (repo=ссылка или owner/repo): "
        "сравнивает публичный проект с исходным кодом JARVIS, применяет только "
        "полезный нативный патч после отдельного AI-ревью, ограниченных проверок "
        "и снапшота с автооткатом; если пользователь прислал только ссылку на "
        "репозиторий, сначала вызови integrate и лишь затем сообщай результат; "
        "ссылка только на профиль не указывает репозиторий — попроси github.com/owner/repo; "
        "не исполняет файлы GitHub и не ставит зависимости; "
        "«что нового»/«что сегодня делал» → journal; «ускорься» → optimize; "
        "«проведи цикл развития» → cycle (один пакет безопасного обслуживания сразу); "
        "«режим простоя» → idle (порог и доступные фоновые задачи)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "op": {
                "type": "STRING",
                "description": "status, idle, models, reflect, repair, scout, adopt, integrate, journal, optimize, cycle",
            },
            "query": {
                "type": "STRING",
                "description": "что искать на GitHub (для scout)",
            },
            "repo": {
                "type": "STRING",
                "description": "GitHub HTTPS-ссылка или owner/repo; для integrate передавай исходную ссылку пользователя",
            },
        },
        "required": ["op"],
    },
    "handler": autonomy,
}
