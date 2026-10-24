"""Military helpers for the HOI4 Millennium Dawn pilot.

Best-effort parsing of unit templates/technologies so the planner knows what
military options exist. GUI-grounded execution will be added later.
"""

from __future__ import annotations

import re
from pathlib import Path


def _roots():
    from actions.hoi4_knowledge import discover_mod_root, discover_game_root

    mod_root, game_root = discover_mod_root(), discover_game_root()
    roots = []
    if mod_root:
        roots.append(mod_root)
    if game_root and game_root != mod_root:
        roots.append(game_root)
    return roots


def available_templates(state: dict) -> list[str]:
    result = []
    for root in _roots():
        folder = root / "common" / "ai_templates"
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            first = re.search(r"(?m)^([ \t]*)([A-Za-z0-9_]+)\s*=\s*\{", text)
            indent = first.group(1) if first else ""
            for match in re.finditer(
                rf"(?m)^{re.escape(indent)}([A-Za-z0-9_]+)\s*=\s*\{{", text
            ):
                name = match.group(1)
                if name in {"role", "upgrade_prio", "target_template", "enable"}:
                    continue
                result.append(name)
    seen = set()
    out = []
    for item in result:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out[:60]


def prepare_military_task(task: dict, state: dict) -> tuple[list[dict], str]:
    if not isinstance(task, dict) or str(task.get("kind", "")).lower() != "military":
        return [], "unsupported task kind"
    template = str(task.get("template_name", "")).strip()
    if not template:
        return [], "missing template_name"
    if template not in available_templates(state):
        return [], "division template is not available"
    return [], "validated: military template available; GUI execution pending"