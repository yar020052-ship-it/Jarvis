"""Inspect and switch Windows power plans, with a reversible undo entry."""

from __future__ import annotations

import os
import re
import subprocess

from core.undo import push_undo

_GUID_RE = re.compile(r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})")
_WINDOWS = os.name == "nt"


def _run(*args: str) -> subprocess.CompletedProcess:
    if not _WINDOWS:
        raise RuntimeError("Переключение схем электропитания доступно только в Windows.")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        ["powercfg", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15,
        creationflags=flags, check=False,
    )


def _active() -> tuple[str, str]:
    result = _run("/getactivescheme")
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Не удалось прочитать активную схему.").strip())
    text = result.stdout.strip()
    match = _GUID_RE.search(text)
    if not match:
        raise RuntimeError("Windows не вернула идентификатор активной схемы питания.")
    name_match = re.search(r"\((.*?)\)", text)
    return match.group(1), name_match.group(1).strip() if name_match else text


def _plans() -> list[tuple[str, str]]:
    result = _run("/list")
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Не удалось получить список схем.").strip())
    plans = []
    for line in result.stdout.splitlines():
        match = _GUID_RE.search(line)
        name_match = re.search(r"\((.*?)\)", line)
        if match:
            plans.append((match.group(1), name_match.group(1).strip() if name_match else "Схема без названия"))
    return plans


def _target_plan(name: str) -> tuple[str, str]:
    key = name.strip().lower().replace("ё", "е").replace(" ", "_")
    plans = _plans()
    if key in {"maximum", "max", "high_performance", "performance", "высокая_производительность", "максимальная_производительность"}:
        for guid, label in plans:
            normalized = label.lower().replace("ё", "е")
            if "производ" in normalized or "high performance" in normalized or "ultimate" in normalized:
                return guid, label
        # SCHEME_MIN is the documented alias for the least power-saving plan.
        return "SCHEME_MIN", "Высокая производительность"
    aliases = {
        "balanced": ("SCHEME_BALANCED", "Сбалансированная"),
        "сбалансированная": ("SCHEME_BALANCED", "Сбалансированная"),
        "power_saver": ("SCHEME_MAX", "Экономия энергии"),
        "saver": ("SCHEME_MAX", "Экономия энергии"),
        "экономия_энергии": ("SCHEME_MAX", "Экономия энергии"),
    }
    if key in aliases:
        alias, fallback = aliases[key]
        # Use the localized plan name/GUID if it is listed by this Windows build.
        needle = "balanced" if "BALANCED" in alias else "эконом" if "MAX" in alias else ""
        for guid, label in plans:
            norm = label.lower().replace("ё", "е")
            if (needle and needle in norm) or ("сбаланс" in norm and "BALANCED" in alias):
                return guid, label
        return alias, fallback
    raise ValueError("Выберите схему: maximum, balanced или power_saver.")


def _activate(guid: str) -> str:
    result = _run("/setactive", guid)
    if result.returncode:
        message = (result.stderr or result.stdout or "Windows отклонила смену схемы.").strip()
        raise RuntimeError(message)
    return result.stdout.strip()


def power_plan(parameters: dict, **_kwargs) -> str:
    params = parameters or {}
    action = str(params.get("action") or "status").strip().lower()
    try:
        if action in {"status", "list"}:
            active_guid, active_name = _active()
            if action == "status":
                return f"Активная схема электропитания: {active_name}."
            options = [f"• {name}{' — активна' if guid.lower() == active_guid.lower() else ''}" for guid, name in _plans()]
            return "Активная схема: " + active_name + "\nДоступные схемы:\n" + "\n".join(options)

        if action != "set":
            return "Доступны действия status, list и set."

        target_name = str(params.get("plan") or "").strip()
        target_guid, display_name = _target_plan(target_name)
        previous_guid, previous_name = _active()
        if target_guid.lower() == previous_guid.lower() or (
            target_guid == "SCHEME_MIN" and "производ" in previous_name.lower()
        ):
            return f"Схема «{previous_name}» уже активна."

        _activate(target_guid)
        current_guid, current_name = _active()
        if target_guid != "SCHEME_MIN" and current_guid.lower() != target_guid.lower():
            return "Windows не подтвердила смену активной схемы. Проверьте настройки электропитания."
        def _restore_previous():
            _activate(previous_guid)
            return f"Восстановлена схема «{previous_name}»."

        push_undo(f"схема электропитания: {previous_name}", _restore_previous)
        return f"Включена схема электропитания «{current_name or display_name}». Предыдущую схему можно восстановить командой отмены."
    except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
        return f"Не удалось изменить схему электропитания: {exc}"


TOOL = {
    "name": "power_plan",
    "description": (
        "Reads and changes Windows power plans. Use status to report the current plan, "
        "list to enumerate plans, and set with plan=maximum | balanced | power_saver. "
        "The change is reversible through the undo tool. If the requested plan is "
        "already active, report that without changing anything."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "status | list | set"},
            "plan": {"type": "STRING", "description": "maximum | balanced | power_saver"},
        },
        "required": [],
    },
    "handler": power_plan,
}
