"""voice_command_library.py — look inside and extend the saved commands.

The engine and the phrase-runner live in actions/voice_command.py; this file is
the book-keeping half: show what is saved, search it, list the ready-made packs,
install one, and show what was run lately. The model calls it when the owner
asks "what commands do I have", "install the YouTube pack" or "what did I run
yesterday" — and to check the list itself when it is unsure whether a request
names a saved phrase.

Writes (install_pack) go through the same library file the fast path reads;
the cache in actions/voice_command.py is invalidated afterwards, so a pack
installed mid-session is live on the next phrase.
"""

from __future__ import annotations

import os
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from actions.voice_command import _invalidate, _library  # noqa: E402
import jarvis_commands as jc  # noqa: E402

_LIST_CHARS = 3200  # keep one listing inside an answerable size


def _command_lines(lib) -> list:
    lines = []
    for folder in lib.folders:
        inner = [c for c in lib.commands if c.folder == folder.id and c.enabled]
        if not inner:
            continue
        lines.append(f"[{folder.name}]")
        for c in inner:
            phrases = ", ".join(c.phrases[:3])
            lines.append(f"• «{c.name}» — {phrases}")
    if not lines:
        lines = ["Библиотека пуста — поставь пак: action=install_pack, value=windows."]
    return lines


def _trim(lines: list) -> str:
    out = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > _LIST_CHARS:
            out.append(f"… ещё {len(lines) - len(out)} строк(и).")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _action_list(params) -> str:
    lib = _library()
    header = (f"Сохранённых команд: {len(lib.commands)} в {len(lib.folders)} "
              f"папках.")
    return header + "\n" + _trim(_command_lines(lib))


def _action_search(params) -> str:
    query = str(params.get("value") or params.get("query") or "").strip()
    if not query:
        return "Не передан запрос (value)."
    lib = _library()
    norm = jc.normalize(query)
    hits = []
    for c in lib.commands:
        hay = " ".join([c.name] + list(c.phrases)).lower().replace("ё", "е")
        if norm and norm in hay:
            hits.append(c)
    if not hits:
        cands = jc.match_phrase(query, lib, threshold=0.45)[:6]
        if cands:
            lines = [f"«{m.command.name}» — {', '.join(m.command.phrases[:3])}"
                     for m in cands]
            return ("Точного совпадения нет. Похожие:\n" + "\n".join(lines))
        return "Ничего не нашлось."
    lines = [f"• «{c.name}» — {', '.join(c.phrases[:3])}" for c in hits[:40]]
    return f"Найдено: {len(hits)}\n" + "\n".join(lines)


def _action_packs(params) -> str:
    import jarvis_packs as jp
    lines = []
    for pack_id, pack in jp.PACKS.items():
        lines.append(f"• {pack_id} — {pack['name']} "
                     f"({len(pack['commands'])} команд): {pack.get('note', '')}")
    return ("Готовые паки (ставятся: action=install_pack, value=<id>):\n"
            + "\n".join(lines))


def _action_install_pack(params) -> str:
    pack_id = str(params.get("value") or "").strip().lower()
    if not pack_id:
        return "Не передан id пака (value). Список — action=packs."
    try:
        stats = jc.install_pack(_library(), pack_id)
    except jc.CommandError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"Не удалось поставить пак: {e}"
    _invalidate()
    return (f"Пак «{pack_id}» установлен: добавлено {stats['added']}, "
            f"обновлено {stats['updated']}, пропущено {stats['skipped']}.")


def _action_create(params) -> str:
    """Build and persist a user-requested voice command from a description."""
    description = str(params.get("description") or params.get("value") or "").strip()
    if not description:
        return "Опишите, что должна делать команда."
    if len(description) > 1600:
        return "Описание слишком длинное (максимум 1600 символов)."
    folder_name = str(params.get("folder") or "").strip()[:100]
    lib = _library()
    folder_id = ""
    if folder_name:
        folder = lib.ensure_folder(folder_name)
        folder_id = folder.id
        lib.save()
    try:
        command = jc.ai_create_command(description, folder_id)
    except jc.CommandError as exc:
        return f"Не удалось создать команду: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось создать команду: {exc}"
    _invalidate()
    phrases = ", ".join(command.phrases[:3]) or "фраза не задана"
    suffix = "; опасные действия будут запрашивать подтверждение" \
        if command.confirm or any(jc._step_is_risky(s) for s in command.steps) else ""
    return f"Сохранена команда «{command.name}» ({len(command.steps)} шагов): {phrases}{suffix}."


def _action_history(params) -> str:
    rows = jc.history(10)
    if not rows:
        return "История пуста."
    lines = []
    for row in rows:
        mark = "ок" if row.get("ok") else "сбой"
        name = row.get("command") or "—"
        lines.append(f"• {row.get('t', '')} [{mark}] {row.get('phrase', '')[:60]}"
                     f" → {name[:60]}")
    return "Последние запуски:\n" + "\n".join(lines)


def _action_status(params) -> str:
    lib = _library()
    packs = {c.origin.get("pack") for c in lib.commands
             if (c.origin or {}).get("pack")}
    from actions.voice_command import FAST_ENV, FAST_SCORE
    fast = os.environ.get(FAST_ENV, "1").strip().lower() not in ("0", "false", "no", "off")
    return (f"Командный центр: {len(lib.commands)} команд, {len(lib.folders)} "
            f"папок, пак(ов) установлено: {len(packs)}.\n"
            f"Файл: {jc.LIB_PATH}\n"
            f"Текстовая быстрая дорожка: {'вкл' if fast else 'выкл'} "
            f"(порог {FAST_SCORE}); история: {len(jc.history(500))} записей.")


_HANDLERS = {
    "list": _action_list,
    "search": _action_search,
    "packs": _action_packs,
    "install_pack": _action_install_pack,
    "create": _action_create,
    "history": _action_history,
    "status": _action_status,
}


def voice_command_library(parameters, player=None):
    action = str((parameters or {}).get("action") or "").strip().lower()
    handler = _HANDLERS.get(action)
    if handler is None:
        return ("Неизвестное действие. Доступно: "
                + ", ".join(_HANDLERS.keys()) + ".")
    try:
        return handler(parameters or {})
    except Exception as e:  # noqa: BLE001 — a tool must never raise at the model
        return f"Сбой библиотеки команд: {e}"


TOOL = {
    "name": "voice_command_library",
    "description": (
        "The owner's saved-commands library (Command Centre): list every saved "
        "phrase, search it by keyword, show the ready-made packs, install one, "
        "create a saved voice command from a natural-language description, "
        "show recent run history, or show its status. Call it when the owner "
        "asks what commands exist, what they can say, which packs are "
        "available, or to add a pack — and call it with action=search when you "
        "are unsure whether a request names a saved phrase before running it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": list(_HANDLERS.keys()),
                "description": (
                    "list = all saved commands; search = find by keyword; "
                    "packs = available packs; install_pack = add a pack by id; "
                    "create = build and save a command from description; "
                    "history = recent runs; status = sizes and file paths."
                ),
            },
            "value": {
                "type": "STRING",
                "description": (
                    "The keyword for search, or the pack id for install_pack. "
                    "Empty for other actions."
                ),
            },
            "description": {
                "type": "STRING",
                "description": "For action=create, describe the command, its trigger phrase, and ordered computer actions in natural language.",
            },
            "folder": {
                "type": "STRING",
                "description": "Optional folder/collection for the new command.",
            },
        },
        "required": ["action"],
    },
    "handler": voice_command_library,
}
