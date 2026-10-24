"""Production helpers for the HOI4 Millennium Dawn pilot.

Best-effort parsing of production/equipment files so the planner knows what
can be built. GUI-grounded execution will be added later.
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


def available_equipment(state: dict) -> list[str]:
    result = []


def prepare_production_task(task: dict, state: dict) -> tuple[list[dict], str]:
    if not isinstance(task, dict) or str(task.get("kind", "")).lower() != "production":
        return [], "unsupported task kind"
    equip = str(task.get("equipment_id", "")).strip()
    if not equip:
        return [], "missing equipment_id"
    if equip not in available_equipment(state):
        return [], "equipment is not available"
    return [], "validated: production target available; GUI execution pending"
    for root in _roots():
        folder = root / "common" / "units" / "equipment"
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for match in re.finditer(r"\b([A-Za-z0-9_.-]+)\s*=\s*\{", text):
                result.append(match.group(1))
    seen = set()
    out = []
    for item in result:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out[:30]