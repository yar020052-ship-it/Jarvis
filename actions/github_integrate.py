"""Inspect and (optionally) vendor a public GitHub repository for JARVIS.

Discovery stays separate from execution: this module only clones or downloads
source files into ``vendor_scout/`` and returns a structured report. It never
imports, runs, or installs anything from the repository. Integration decisions
remain explicit and reviewable.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.paths import get_app_dir

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MAX_FILE_BYTES = 200_000
_DEFAULT_FILES = ("README.md", "README.rst", "pyproject.toml", "requirements.txt", "setup.py", "package.json")


def _normalize_repo(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("http"):
        parsed = urlparse(value)
        if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            return None
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) < 2:
            return None
        value = f"{parts[0]}/{parts[1]}"
    value = value.removesuffix(".git")
    return value if _REPO_RE.fullmatch(value) else None


def _safe_vendor_dir(repo: str) -> Path:
    owner, name = repo.split("/", 1)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{owner}-{name}")
    return get_app_dir() / "vendor_scout" / safe


def _read_raw(repo: str, path: str) -> str | None:
    clean = path.strip().lstrip("/")
    if not clean or ".." in clean.split("/") or clean.startswith("/"):
        return None
    url = f"https://raw.githubusercontent.com/{repo}/HEAD/{clean}"
    try:
        import requests

        response = requests.get(url, timeout=(8, 20), headers={"User-Agent": "JARVIS-GitHub-Integrate/1.0"})
        if response.status_code != 200:
            return None
        content = response.content
        if len(content) > _MAX_FILE_BYTES:
            return f"[file too large: {len(content)} bytes]"
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return f"[binary or non-UTF-8 file: {len(content)} bytes]"
    except Exception as exc:  # noqa: BLE001
        return f"[download failed: {exc}]"


def _clone(repo: str, destination: Path) -> str:
    if shutil.which("git") is None:
        return "git is not installed; source files were read through the GitHub API instead."
    if destination.exists():
        return f"Repository already vendored at {destination}."
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", str(destination)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "Cloning timed out after 180 seconds."
    if result.returncode != 0:
        return f"Clone failed: {(result.stderr or result.stdout).strip()[:500]}"
    return f"Cloned into {destination}."


def _top_level(destination: Path) -> list[str]:
    if not destination.exists():
        return []
    try:
        return sorted(item.name for item in destination.iterdir())[:80]
    except OSError:
        return []


def github_integrate(parameters: dict | None = None, **_kwargs) -> str:
    params = parameters or {}
    repo = _normalize_repo(str(params.get("repo") or params.get("repository") or ""))
    if not repo:
        return "Укажите репозиторий в виде owner/name или ссылку https://github.com/owner/name."

    goal = str(params.get("goal") or params.get("task") or "").strip()
    clone = bool(params.get("clone", True))
    requested_files = params.get("files")
    if isinstance(requested_files, str):
        requested_files = [item.strip() for item in requested_files.split(",") if item.strip()]
    if not isinstance(requested_files, list):
        requested_files = list(_DEFAULT_FILES)

    destination = _safe_vendor_dir(repo)
    lines = [f"Репозиторий: {repo}", f"Цель интеграции: {goal or 'не указана'}"]
    if clone:
        lines.append(_clone(repo, destination))
        lines.append(f"Локальная копия: {destination}")
    else:
        lines.append("Локальная копия не создана (clone=false).")

    structure = _top_level(destination)
    if structure:
        lines.append("Верхний уровень vendored-копии:")
        lines.append(", ".join(structure))

    lines.append("Прочитанные файлы:")
    for relative in requested_files[:12]:
        content = _read_raw(repo, str(relative))
        if content is None:
            continue
        lines.append(f"\n===== {relative} =====\n{content[:_MAX_FILE_BYTES]}")

    lines.append(
        "\nДальше: изучите прочитанный код и установите выбранный файл через "
        "self_integrate (source должен указывать внутрь vendor_scout/), затем "
        "проверьте health-check. Этот инструмент сам ничего не запускает и не устанавливает."
    )
    return "\n".join(lines)


TOOL = {
    "name": "github_integrate",
    "description": (
        "Inspect a public GitHub repository for possible JARVIS integration. Accepts "
        "owner/name or a GitHub URL, clones it into vendor_scout/ by default (set "
        "clone=false to skip), reads key files without executing anything, and returns a "
        "structured report. Use this "
        "when the user asks to analyze or adopt a feature from GitHub. This tool only "
        "inspects sources; install one reviewed file with self_integrate afterwards."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "repo": {"type": "STRING", "description": "GitHub repository as owner/name or URL"},
            "repository": {"type": "STRING", "description": "Alias for repo"},
            "goal": {"type": "STRING", "description": "What feature or capability to evaluate"},
            "task": {"type": "STRING", "description": "Alias for goal"},
            "clone": {"type": "BOOLEAN", "description": "Clone the repository into vendor_scout/ for local inspection"},
            "files": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Optional repository-relative files to read",
            },
        },
        "required": ["repo"],
    },
    "handler": github_integrate,
}