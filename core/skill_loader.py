"""Discover and safely load Codex-style local Agent Skills for JARVIS.

Each skill is a directory containing a ``SKILL.md`` file. Only the name and
short description are added to the always-on prompt; full instructions are
loaded on demand by the ``load_skill`` action.
"""

from __future__ import annotations

import re
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_SKILL_BYTES = 24_000


def _skills_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "skills"


def _parse(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if len(raw.encode("utf-8")) > _MAX_SKILL_BYTES:
        return None

    name = path.parent.name
    description = ""
    body = raw
    if raw.startswith("---\n"):
        _, _, rest = raw.partition("\n---\n")
        if not rest:
            return None
        body = rest
        for line in raw.split("\n---\n", 1)[0].splitlines()[1:]:
            key, sep, value = line.partition(":")
            if not sep:
                continue
            value = value.strip().strip('"').strip("'")
            if key.strip().lower() == "name":
                name = value
            elif key.strip().lower() == "description":
                description = value
    if not _NAME_RE.fullmatch(name) or not description:
        return None
    return {"name": name, "description": description, "body": body.strip()}


def list_skills() -> list[dict]:
    root = _skills_dir()
    if not root.is_dir():
        return []
    found = []
    for manifest in sorted(root.glob("*/SKILL.md")):
        skill = _parse(manifest)
        if skill:
            found.append({"name": skill["name"], "description": skill["description"]})
    return found


def load_skill(name: str) -> str:
    name = str(name or "").strip()
    if not _NAME_RE.fullmatch(name):
        return "Invalid skill name."
    root = _skills_dir().resolve()
    manifest = (root / name / "SKILL.md").resolve()
    if root not in manifest.parents:
        return "Invalid skill path."
    skill = _parse(manifest)
    if not skill or skill["name"] != name:
        return f"Skill '{name}' is not installed or has an invalid manifest."
    return f"Skill: {skill['name']}\n{skill['description']}\n\n{skill['body']}"
