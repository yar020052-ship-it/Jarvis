"""Safely install a reviewed file from vendor_scout/ into the JARVIS project.

This is the "apply" half of GitHub adoption: after ``github_integrate`` has
fetched a repository and the assistant/user has reviewed it, this action copies
one chosen file into the project, backs up the previous version, and runs the
health check. It never executes repository code and refuses to overwrite core
entry points without an explicit confirmation path.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from core.paths import get_app_dir

_ALLOWED_TARGET_PREFIXES = ("actions/", "plugins/", "utils/", "skills/", "docs/", "core/")
_BLOCKED_TARGETS = {"main.py", "ui.py", "pyproject.toml", "requirements.txt"}
_MAX_COPY_BYTES = 1_000_000


def _safe_rel(value: str) -> str | None:
    value = (value or "").strip().replace("\\", "/")
    if not value or value.startswith("/") or ".." in value.split("/"):
        return None
    return value


def self_integrate(parameters: dict | None = None, **_kwargs) -> str:
    params = parameters or {}
    source_rel = _safe_rel(str(params.get("source") or params.get("source_path") or ""))
    target_rel = _safe_rel(str(params.get("target") or params.get("target_path") or ""))
    overwrite = bool(params.get("overwrite", False))

    if not source_rel or not target_rel:
        return "Укажите source и target как пути относительно корня проекта/vendor_scout."

    root = get_app_dir()
    source = (root / source_rel).resolve()
    target = (root / target_rel).resolve()
    if root not in source.parents and source != root:
        return "Источник должен находиться внутри проекта."
    if root not in target.parents and target != root:
        return "Цель должна находиться внутри проекта."
    if not source.is_file():
        return f"Исходный файл не найден: {source_rel}"
    if source.stat().st_size > _MAX_COPY_BYTES:
        return "Файл слишком большой для автоматической установки."
    if target.name in _BLOCKED_TARGETS:
        return f"Автоматическое изменение {target.name} запрещено."
    if not any(target_rel.startswith(prefix) for prefix in _ALLOWED_TARGET_PREFIXES):
        return "Можно устанавливать только в actions/, plugins/, utils/, skills/, docs/ или core/."
    if target.exists() and not overwrite:
        return f"Целевой файл уже существует: {target_rel}. Повторите с overwrite=true после review."

    backup_dir = root / ".backup" / f"self-integrate-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    backup_path = None
    if target.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / target.name
        shutil.copy2(target, backup_path)

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)

    try:
        from autonomy import health

        report = health.run_health()
        if not report.ok:
            if backup_path is not None:
                shutil.copy2(backup_path, target)
            return "Установка отменена: health-check не прошёл. Изменение откачено."
    except Exception as exc:  # noqa: BLE001
        if backup_path is not None:
            shutil.copy2(backup_path, target)
        return f"Установка отменена: ошибка health-check ({exc}). Изменение откачено."

    result = f"Установлен {source_rel} -> {target_rel}."
    if backup_path is not None:
        result += f" Резервная копия: {backup_path}."
    result += " Проверьте изменения и перезапустите JARVIS при необходимости."
    return result


TOOL = {
    "name": "self_integrate",
    "description": (
        "Install one reviewed file from the project (usually vendor_scout/) into an "
        "allowed project directory with a backup and health check. Use only after "
        "reviewing repository contents from github_integrate. Dangerous entry points "
        "are blocked; existing files require overwrite=true."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "source": {"type": "STRING", "description": "Source file relative to the project root"},
            "target": {"type": "STRING", "description": "Destination file relative to the project root"},
            "source_path": {"type": "STRING", "description": "Alias for source"},
            "target_path": {"type": "STRING", "description": "Alias for target"},
            "overwrite": {"type": "BOOLEAN", "description": "Overwrite an existing destination file"},
        },
        "required": ["source", "target"],
    },
    "handler": self_integrate,
}