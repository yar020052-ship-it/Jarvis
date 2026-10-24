"""Diplomacy helpers for the HOI4 Millennium Dawn pilot.

Best-effort parsing of diplomatic actions/ideas so the planner knows what
options exist. GUI-grounded execution will be added later.
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


def available_diplomatic_actions(state: dict) -> list[str]:
    result = []


def prepare_diplomacy_task(task: dict, state: dict) -> tuple[list[dict], str]:
    if not isinstance(task, dict) or str(task.get("kind", "")).lower() != "diplomacy":
        return [], "unsupported task kind"
    action = str(task.get("action_id", "")).strip()
    if not action:
        return [], "missing action_id"
    if action not in available_diplomatic_actions(state):
        return [], "diplomatic action is not available"
    return [], "validated: diplomatic action available; GUI execution pending"
    for root in _roots():
        for sub in ("common/decisions", "common/ai_strategy"):
            folder = root / sub
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