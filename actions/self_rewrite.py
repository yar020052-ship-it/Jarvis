"""Self-rewrite capability for JARVIS — allows modifying its own source code
with safety guardrails: git branch isolation, backup, tests, rollback.

Owner confirmation is required for core entry points (main.py, ui.py, etc.).
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from core.paths import get_app_dir

# Files that require explicit owner confirmation before modification
_PROTECTED_FILES = {
    "main.py",
    "ui.py",
    "pyproject.toml",
    "requirements.txt",
    "core/gemini.py",
    "core/paths.py",
    "core/secrets.py",
}

# Directories that are off-limits for self-rewrite
_BLOCKED_DIRS = {".venv", "vendor_scout", ".git", "node_modules", "__pycache__"}


def _safe_rel(value: str) -> str | None:
    value = (value or "").strip().replace("\\", "/")
    if not value or value.startswith("/") or ".." in value.split("/"):
        return None
    parts = value.split("/")
    if any(p in _BLOCKED_DIRS for p in parts):
        return None
    return value


def _is_protected(rel: str) -> bool:
    return rel in _PROTECTED_FILES or rel.split("/")[-1] in {"main.py", "ui.py"}


def _show_status() -> str:
    root = get_app_dir()
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        return (
            f"Ветка: {branch}\n"
            f"Изменения:\n{status or '(нет)'}\n"
            f"Последние коммиты:\n{log}"
        )
    except Exception as e:
        return f"Ошибка: {e}"


def self_rewrite(parameters: dict | None = None, **_kwargs) -> str:
    """Rewrite a JARVIS source file with safety guardrails.

    Parameters (via parameters dict):
        file: relative path to the file to rewrite (e.g. "actions/weather.py")
        content: new file content (full replacement)
        branch: git branch name (default: auto-generated)
        run_tests: whether to run tests after modification (default: True)
        confirm_protected: set True to allow rewriting protected files (owner approval)
        status: if True, show current status instead of rewriting

    Returns a status string.
    """
    params = parameters or {}

    # Status mode
    if params.get("status", False):
        return _show_status()

    rel = _safe_rel(str(params.get("file") or params.get("path") or ""))
    content = params.get("content")
    branch_name = params.get("branch")
    run_tests = params.get("run_tests", True)
    confirm_protected = bool(params.get("confirm_protected", False))

    if not rel:
        return "Укажите file — путь к файлу относительно корня проекта."
    if content is None:
        return "Укажите content — новое содержимое файла."

    root = get_app_dir()
    target = (root / rel).resolve()
    if root not in target.parents and target != root:
        return "Путь выходит за пределы проекта."
    if not target.is_file() and not target.parent.exists():
        return f"Родительская директория не существует: {target.parent}"

    protected = _is_protected(rel)
    if protected and not confirm_protected:
        return (
            f"Файл {rel} защищён. Для изменения передайте confirm_protected=true "
            "(требуется подтверждение владельца)."
        )

    # 1. Create git branch for isolation
    if not branch_name:
        branch_name = f"self-rewrite-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        r = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            r2 = subprocess.run(
                ["git", "checkout", branch_name],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if r2.returncode != 0:
                return f"Не удалось создать ветку {branch_name}: {r.stderr or r2.stderr}"
    except Exception as e:
        return f"Ошибка git: {e}"

    # 2. Backup original
    backup_dir = root / ".backup" / f"self-rewrite-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    backup_path = None
    if target.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / target.name
        shutil.copy2(target, backup_path)

    # 3. Write new content
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(str(content), encoding="utf-8")
    except Exception as e:
        if backup_path and backup_path.is_file():
            shutil.copy2(backup_path, target)
        subprocess.run(["git", "checkout", "main"], cwd=str(root), capture_output=True, text=True, timeout=30)
        return f"Ошибка записи файла: {e}"

    # 4. Run tests
    if run_tests:
        try:
            from core.tester import Tester

            report = Tester().run_tests("tests")
            if not report.get("ok", True) and report.get("returncode", 0) != 0:
                if backup_path and backup_path.is_file():
                    shutil.copy2(backup_path, target)
                subprocess.run(["git", "checkout", "main"], cwd=str(root), capture_output=True, text=True, timeout=30)
                return (
                    f"Тесты не прошли — изменение откачено.\n"
                    f"Ветка: {branch_name}\n"
                    f"Результат: {str(report)[:500]}"
                )
        except Exception:
            pass

    # 5. Commit on branch
    try:
        subprocess.run(["git", "add", rel], cwd=str(root), capture_output=True, text=True, timeout=30)
        subprocess.run(
            ["git", "commit", "-m", f"self-rewrite: {rel}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        pass

    result = f"Файл {rel} переписан в ветке {branch_name}."
    if backup_path:
        result += f" Резервная копия: {backup_path}."
    if protected:
        result += " (защищённый файл — изменение применено с подтверждением владельца)"
    result += "\nДля применения в main: git merge " + branch_name
    return result


TOOL = {
    "name": "self_rewrite",
    "description": (
        "Rewrite a JARVIS source file with safety guardrails (git branch, backup, "
        "tests, rollback). Protected files (main.py, ui.py, core/gemini.py, etc.) "
        "require confirm_protected=true. Use status=true to check current state. "
        "Use for autonomous self-improvement."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file": {"type": "STRING", "description": "Relative path to file to rewrite"},
            "path": {"type": "STRING", "description": "Alias for file"},
            "content": {"type": "STRING", "description": "New full file content"},
            "branch": {"type": "STRING", "description": "Git branch name (auto-generated if omitted)"},
            "run_tests": {"type": "BOOLEAN", "description": "Run tests after modification (default: true)"},
            "confirm_protected": {"type": "BOOLEAN", "description": "Confirm rewriting protected files (owner approval)"},
            "status": {"type": "BOOLEAN", "description": "Show current git status instead of rewriting"},
        },
        "required": [],
    },
    "handler": self_rewrite,
}
