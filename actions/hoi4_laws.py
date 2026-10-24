"""Laws and decisions helpers for the HOI4 Millennium Dawn pilot.

Parses installed law/decision files and exposes currently available options.
GUI-grounded execution will be added later.
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


def available_laws(state: dict) -> list[str]:
    """Return law/decision IDs mentioned in installed files (best-effort)."""


def _laws_gui() -> str:
    from actions.hoi4_knowledge import discover_mod_root, discover_game_root

    roots = []
    mod_root = discover_mod_root()
    game_root = discover_game_root()
    if mod_root:
        roots.append(mod_root)
    if game_root and game_root != mod_root:
        roots.append(game_root)
    for root in roots:
        path = root / "interface" / "politicsview.gui"
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
    return ""


def prepare_law_task(task: dict, state: dict) -> tuple[list[dict], str]:
    if not isinstance(task, dict) or str(task.get("kind", "")).lower() not in ("change_law", "decision"):
        return [], "unsupported task kind"
    law_id = str(task.get("law_id", task.get("decision_id", ""))).strip()
    if not law_id:
        return [], "missing law/decision id"
    if law_id not in available_laws(state):
        return [], "law/decision is not available"
    gui = _laws_gui()
    if not gui:
        return [], "validated: law/decision available; GUI not found"
    return [], "validated: law/decision available; GUI execution pending"
    result = []
    for root in _roots():
        for sub in ("common/ideas", "common/decisions"):
            folder = root / sub
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*.txt")):
                try:
                    text = path.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                for match in re.finditer(r"\b(id|name)\s*=\s*([A-Za-z0-9_.-]+)", text):
                    result.append(match.group(2))
    # De-duplicate.
    seen = set()
    out = []
    for item in result:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out[:30]