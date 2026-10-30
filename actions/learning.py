"""Owner-facing control over the lessons JARVIS learns from its mistakes.

This is deliberately separate from ``save_memory``.  A memory entry says what is
true ("the owner prefers dark mode"); a lesson says how to behave ("do not close
the browser when the owner asks for a new tab").  The model is told to use the
right one, and the owner can inspect or delete what was learned automatically.
"""

from __future__ import annotations

from memory import lessons as store

_KIND_LABELS = {
    "explicit": "правило",
    "correction": "поправка",
    "reflection": "вывод",
    "tool_failure": "сбой инструмента",
    "unknown_tool": "несуществующий инструмент",
}


def _fmt(item: dict, index: int) -> str:
    kind = _KIND_LABELS.get(str(item.get("kind") or ""), "урок")
    scope = str(item.get("scope") or "general")
    text = str(item.get("fix") or item.get("mistake") or "")[:170]
    where = f" [{scope}]" if scope != "general" else ""
    hits = int(item.get("hits") or 1)
    repeat = f", повторений {hits}" if hits > 1 else ""
    return f"{index}. {kind}{where}: {text}{repeat} (id {item.get('id')})"


def learning(parameters: dict | None = None, **_ctx) -> str:
    params = parameters or {}
    op = str(params.get("op", "list")).strip().lower()

    if op in {"remember", "add", "teach"}:
        rule = str(params.get("rule") or params.get("fix") or "").strip()
        trigger = str(params.get("trigger") or "").strip()
        scope = str(params.get("scope") or "general").strip()
        item = store.remember_rule(rule, trigger=trigger, scope=scope)
        if not item:
            return "Нечего запоминать: сформулируй правило одной фразой."
        tail = f" Когда: {trigger}." if trigger else ""
        return f"Запомнил правило: {item.get('fix')}{tail}"

    if op in {"list", "show", "mistakes"}:
        try:
            limit = int(params.get("limit") or 8)
        except (TypeError, ValueError):
            limit = 8
        items = store.list_recent(limit)
        if not items:
            return (
                "Пока нет уроков. Когда я ошибусь или ты меня поправишь, "
                "я сохраню вывод и не повторю эту ошибку."
            )
        summary = store.stats()
        head = (
            f"Уроков: {summary['total']} "
            f"(открытых сбоев {summary['open']}, правил владельца {summary['owner']})."
        )
        return head + "\n" + "\n".join(_fmt(item, i) for i, item in enumerate(items, 1))

    if op in {"stats", "status"}:
        summary = store.stats()
        if not summary["total"]:
            return "Пока нет уроков: я ещё не зафиксировал ни одной своей ошибки."
        kinds = summary.get("kinds") or {}
        parts = [
            f"Всего уроков: {summary['total']}.",
            f"Открытых сбоев: {summary['open']}.",
            f"Правил и поправок владельца: {summary['owner']}.",
            f"Выводов после сессий: {kinds.get('reflection', 0)}.",
        ]
        if summary.get("last_updated"):
            parts.append(f"Последнее обновление: {summary['last_updated']}.")
        return " ".join(parts)

    if op in {"forget", "delete", "remove"}:
        ref = str(
            params.get("id")
            or params.get("query")
            or params.get("rule")
            or params.get("ref")
            or ""
        ).strip()
        if not ref:
            return "Укажи id урока или фразу из него."
        removed = store.forget(ref)
        if not removed:
            return f"Не нашёл урок по запросу «{ref}»."
        return f"Забыл {removed} урок(ов) по запросу «{ref}»."

    if op in {"clear", "reset"}:
        removed = store.clear_auto()
        if not removed:
            return "Нечего очищать: автоматических уроков нет."
        return (
            f"Убрал {removed} автоматически выученных урок(ов). "
            "Правила и поправки, которые ты дал сам, остались."
        )

    raise RuntimeError(
        "op должен быть: remember, list, stats, forget или clear."
    )


TOOL = {
    "name": "learning",
    "description": (
        "Поведенческое обучение JARVIS на ошибках. Используй, когда владелец "
        "поправляет тебя («не то», «я же просил», «больше так не делай») или "
        "просит запомнить правило поведения; также когда ты сам понял, что "
        "предыдущий способ действий был неверным. Для фактов и предпочтений "
        "используй save_memory, а не этот инструмент. op=remember (rule, "
        "trigger, scope), op=list (последние уроки), op=stats (счётчики), "
        "op=forget (id или фраза), op=clear (удалить автоматические уроки, "
        "сохранив правила владельца)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "op": {
                "type": "STRING",
                "description": "remember, list, stats, forget или clear",
            },
            "rule": {
                "type": "STRING",
                "description": "Правило поведения для op=remember: что делать или чего не делать",
            },
            "trigger": {
                "type": "STRING",
                "description": "Необязательное условие: когда именно применять правило",
            },
            "scope": {
                "type": "STRING",
                "description": "Имя инструмента или general; по умолчанию general",
            },
            "id": {
                "type": "STRING",
                "description": "id урока для op=forget",
            },
            "query": {
                "type": "STRING",
                "description": "Фраза из урока для op=forget, если id неизвестен",
            },
            "limit": {
                "type": "INTEGER",
                "description": "Сколько уроков показать для op=list (1–30)",
            },
        },
        "required": ["op"],
    },
    "handler": learning,
}
