"""Law catalog and selection for the HOI4 Millennium Dawn pilot.

Laws in HOI4 are idea groups marked with ``law = yes`` inside
``common/ideas``.  Millennium Dawn replaces that folder, so when the mod is
installed its definitions are the only ones that matter.  This module parses
the installed groups, scores their variants by the modifiers that actually
appear in the files, and exposes a small typed task:

    {"kind": "change_law", "law_id": "<variant id>"}

The task is executed by ``actions.hoi4_law_gui``: it finds the *currently
active* law icon in the politics view, opens the group and clicks the target
icon only after a screenshot match.  No blind coordinates are ever used.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

_GROUP_CACHE: dict[str, object] = {}

# Groups where the weighted modifier score is a reliable improvement signal, so
# the pilot may change them even when the planner stays silent.  Every other
# group stays under explicit planner control.
AUTO_GROUPS = {"economic_cycles", "corruption"}

# Modifier keys that make a law better (positive sign) or worse (negative).
# Values are the relative weight; the first matching token wins.
_POSITIVE_WEIGHTS = (
    ("stability", 1.0),
    ("war_support", 1.0),
    ("political_power", 0.8),
    ("productivity", 1.0),
    ("factory_output", 1.0),
    ("industrial_capacity", 1.0),
    ("construction", 0.8),
    ("research", 0.8),
    ("production", 0.6),
    ("manpower", 0.8),
    ("recruitable", 0.8),
    ("fuel", 0.6),
    ("resource", 0.5),
    ("gdp", 0.8),
    ("income", 0.6),
    ("tax", 0.6),
    ("planning", 0.4),
    ("army", 0.4),
    ("division", 0.4),
    ("air", 0.4),
    ("naval", 0.3),
    ("supply", 0.4),
    ("intel", 0.3),
    ("opinion", 0.2),
)
_NEGATIVE_WEIGHTS = (
    ("consumer_goods", 1.0),
    ("corruption", 1.0),
    ("inflation", 1.0),
    ("debt", 1.0),
    ("unrest", 1.0),
    ("attrition", 0.8),
    ("resistance", 0.6),
    ("cost", 0.7),
    ("upkeep", 0.4),
    ("build_time", 0.5),
    ("research_time", 0.5),
    ("training_time", 0.4),
)


def _roots() -> list[tuple[str, Path]]:
    from actions.hoi4_knowledge import discover_game_root, discover_mod_root

    roots: list[tuple[str, Path]] = []
    mod_root = discover_mod_root()
    game_root = discover_game_root()
    if mod_root:
        roots.append(("mod", mod_root))
    if game_root and game_root != mod_root:
        roots.append(("base", game_root))
    return roots


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _scalar(text: str, key: str) -> str | None:
    match = re.search(
        rf"(?im)^\s*{re.escape(key)}\s*=\s*(?:\"([^\"]*)\"|([^\s#}}]+))", text
    )
    if not match:
        return None
    return (match.group(1) if match.group(1) is not None else match.group(2)).strip()


def _matching_brace(text: str, open_at: int) -> int | None:
    from actions.hoi4_live_state import _matching_brace as brace

    return brace(text, open_at)


def _direct_children(block: str) -> list[dict]:
    """Return direct ``id = { ... }`` children of one idea block."""
    first = re.search(r"(?m)^([ \t]+)([A-Za-z0-9_-]+)\s*=\s*\{", block[1:])
    if first is None:
        return []
    indent = first.group(1)
    rows: list[dict] = []
    for match in re.finditer(
        rf"(?m)^{re.escape(indent)}([A-Za-z0-9_-]+)\s*=\s*\{{", block
    ):
        open_at = block.find("{", match.start(), match.end())
        close_at = _matching_brace(block, open_at)
        if close_at is None:
            continue
        rows.append({
            "id": match.group(1),
            "body": block[open_at:close_at + 1],
            "start": match.start(),
        })
    return rows


def _infer_level(variant_id: str, explicit: int | None) -> int | None:
    """Use an explicit ``level`` or a trailing numeric tier in the id."""
    if explicit is not None:
        return explicit
    match = re.search(r"(?:^|_)(\d{1,2})$", variant_id)
    return int(match.group(1)) if match else None


def _modifier_values(body: str) -> dict[str, float]:
    match = re.search(r"(?im)^\s*modifier\s*=\s*\{", body)
    if match is None:
        return {}
    open_at = body.find("{", match.start(), match.end())
    close_at = _matching_brace(body, open_at)
    if close_at is None:
        return {}
    block = body[open_at + 1:close_at]
    values: dict[str, float] = {}
    for item in re.finditer(
        r"(?m)^\s*([A-Za-z0-9_.]+)\s*=\s*(-?[\d.]+)\s*$", block
    ):
        try:
            values[item.group(1)] = float(item.group(2))
        except ValueError:
            continue
    return values


def _modifier_score(modifiers: dict[str, float]) -> float:
    total = 0.0
    for key, value in modifiers.items():
        lowered = key.lower()
        weight = 0.0
        sign = 1.0
        for token, token_weight in _POSITIVE_WEIGHTS:
            if token in lowered:
                weight = token_weight
                break
        if not weight:
            for token, token_weight in _NEGATIVE_WEIGHTS:
                if token in lowered:
                    weight = token_weight
                    sign = -1.0
                    break
        if not weight:
            continue
        total += sign * max(-1.0, min(1.0, value)) * weight
    return round(total, 4)


def _ideas_root() -> tuple[str, Path] | None:
    """Return the active ideas folder (MD replaces the base-game one)."""
    for label, root in _roots():
        folder = root / "common" / "ideas"
        if not folder.is_dir():
            continue
        if _root_has_law(folder):
            return label, folder
    return None


def _root_has_law(folder: Path) -> bool:
    for path in folder.rglob("*.txt"):
        if re.search(r"(?m)^\s*law\s*=\s*yes", _read(path)):
            return True
    return False


def _parse_groups(label: str, folder: Path) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for path in sorted(folder.rglob("*.txt")):
        text = _read(path)
        if not text or "law" not in text:
            continue
        for ideas_match in re.finditer(r"(?im)^\s*ideas\s*=\s*\{", text):
            open_at = text.find("{", ideas_match.start(), ideas_match.end())
            close_at = _matching_brace(text, open_at)
            if close_at is None:
                continue
            block = text[open_at:close_at + 1]
            for child in _direct_children(block):
                body = child["body"]
                if not re.search(r"(?im)^\s*law\s*=\s*yes", body):
                    continue
                variants: dict[str, dict] = {}
                ordered: list[str] = []
                for variant in _direct_children(body):
                    vbody = variant["body"]
                    modifiers = _modifier_values(vbody)
                    level_raw = _scalar(vbody, "level")
                    cost_raw = _scalar(vbody, "cost")
                    if not modifiers and level_raw is None and cost_raw is None:
                        continue
                    try:
                        level = int(level_raw) if level_raw is not None else None
                    except ValueError:
                        level = None
                    level = _infer_level(variant["id"], level)
                    tags = sorted(set(re.findall(
                        r"(?im)\boriginal_tag\s*=\s*([A-Z]{3})\b", vbody
                    )))
                    try:
                        cost = int(float(cost_raw)) if cost_raw is not None else None
                    except ValueError:
                        cost = None
                    ai_factor = None
                    ai_match = re.search(
                        r"(?is)ai_will_do\s*=\s*\{[^}]*?factor\s*=\s*(-?[\d.]+)", vbody
                    )
                    if ai_match:
                        try:
                            ai_factor = float(ai_match.group(1))
                        except ValueError:
                            ai_factor = None
                    variants[variant["id"]] = {
                        "id": variant["id"],
                        "level": level,
                        "cost": cost,
                        "tags": tags,
                        "picture": _scalar(vbody, "picture"),
                        "ai_will_do": ai_factor,
                        "modifiers": modifiers,
                        "score": _modifier_score(modifiers),
                        "group": child["id"],
                        "source": path.name,
                    }
                    ordered.append(variant["id"])
                if not variants:
                    continue
                groups[child["id"]] = {
                    "id": child["id"],
                    "source": path.name,
                    "origin": label,
                    "use_list_view": bool(
                        re.search(r"(?im)^\s*use_list_view\s*=\s*yes", body)
                    ),
                    "variants": variants,
                    "ordered": ordered,
                }
    return groups


def law_groups(force: bool = False) -> dict[str, dict]:
    """Parsed law groups from the idea files the game would actually load."""
    found = _ideas_root()
    if found is None:
        return {}
    label, folder = found
    signature = []
    for path in sorted(folder.rglob("*.txt")):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((str(path), stat.st_size, stat.st_mtime_ns))
    key = hashlib.sha1(repr(signature).encode("utf-8", "replace")).hexdigest()
    if not force and _GROUP_CACHE.get("signature") == key:
        return _GROUP_CACHE["groups"]  # type: ignore[return-value]
    groups = _parse_groups(label, folder)
    _GROUP_CACHE["signature"] = key
    _GROUP_CACHE["groups"] = groups
    return groups


def _group_for(law_id: str) -> dict | None:
    for group in law_groups().values():
        if law_id in group["variants"]:
            return group
    return None


def _active_ideas(state: dict) -> list[str]:
    ideas = state.get("ideas")
    if isinstance(ideas, dict):
        return [str(item) for item in ideas.get("active") or []]
    if isinstance(ideas, list):
        return [str(item) for item in ideas]
    return []


def law_group_for(law_id: str) -> dict | None:
    """Public lookup: law variant id -> parsed group definition."""
    return _group_for(str(law_id or ""))


def current_laws(state: dict) -> dict[str, str]:
    """Map group id -> currently active variant id, from the save's ideas list."""
    active = set(_active_ideas(state))
    result: dict[str, str] = {}
    for group_id, group in law_groups().items():
        for variant_id in group["ordered"]:
            if variant_id in active:
                result[group_id] = variant_id
                break
    return result


def available_laws(state: dict | None = None) -> list[str]:
    """Every law variant id installed in the active ideas folder."""
    return sorted(
        variant_id
        for group in law_groups().values()
        for variant_id in group["variants"]
    )


def _adjacent(current: dict, target: dict) -> bool:
    a, b = current.get("level"), target.get("level")
    if a is None or b is None:
        # Without a numeric tier there is no grounded notion of "one step".
        return False
    return abs(int(a) - int(b)) <= 1


def _allowed_for_player(variant: dict, player: str) -> bool:
    """Reject variants visibly reserved for another country."""
    player = (player or "").upper()
    tags = {str(tag).upper() for tag in variant.get("tags") or []}
    if tags and player not in tags:
        return False
    prefix = re.match(r"^([A-Z]{3})_", variant.get("id") or "")
    if prefix and player and prefix.group(1) != player:
        return False
    return True


def law_candidates(state: dict, pending: list | None = None) -> list[dict]:
    """Ranked, adjacent, score-improving law changes grounded in the files."""
    pending = pending or []
    blocked = {
        str(item.get("law_id"))
        for item in pending
        if isinstance(item, dict) and item.get("kind") == "change_law"
    }
    current = current_laws(state)
    player = str(state.get("player") or "")
    candidates: list[dict] = []
    for group_id, current_id in current.items():
        group = law_groups().get(group_id)
        if not group:
            continue
        source = group["variants"].get(current_id)
        if not source:
            continue
        for target_id in group["ordered"]:
            if target_id == current_id or target_id in blocked:
                continue
            target = group["variants"][target_id]
            if not _allowed_for_player(target, player):
                continue
            if not _adjacent(source, target):
                continue
            delta = round(target["score"] - source["score"], 3)
            if delta <= 0.15:
                continue
            candidates.append({
                "kind": "change_law",
                "law_id": target_id,
                "law_group": group_id,
                "law_current": current_id,
                "delta": delta,
                "level": target.get("level"),
                "cost": target.get("cost"),
                "score": target["score"],
            })
    candidates.sort(key=lambda item: (-item["delta"], item["cost"] or 0))
    return candidates


def best_law_task(state: dict, pending: list | None = None) -> dict | None:
    """A single strong fallback law change, or ``None`` when nothing is clear."""
    candidates = law_candidates(state, pending)
    candidates = [item for item in candidates if item["law_group"] in AUTO_GROUPS]
    if not candidates:
        return None
    best = candidates[0]
    if best["delta"] < 0.3:
        return None
    return {
        "kind": "change_law",
        "law_id": best["law_id"],
        "law_group": best["law_group"],
        "reason": (
            f"{best['law_current']} -> {best['law_id']} adds "
            f"{best['delta']:+.2f} weighted modifier score"
        ),
    }


def law_task_context(state: dict, pending: list | None = None) -> str:
    """Prompt context: parsed law groups, current laws and safe alternatives."""
    groups = law_groups()
    if not groups:
        return ""
    current = current_laws(state)
    candidates = law_candidates(state, pending)
    by_group: dict[str, list[dict]] = {}
    for item in candidates:
        by_group.setdefault(item["law_group"], []).append(item)
    lines = [
        "FILE-GROUNDED LAW STATE:",
        f"- Parsed law groups: {len(groups)}; active laws found in the save: {len(current)}.",
    ]
    if not current:
        lines.append(
            "- The save does not list an active variant for any parsed law group; "
            "law changes cannot be anchored to a visible current-law icon and are skipped."
        )
        return "\n".join(lines)
    for group_id in sorted(current, key=lambda item: len(by_group.get(item, [])), reverse=True):
        current_id = current[group_id]
        variant = groups[group_id]["variants"].get(current_id, {})
        level = variant.get("level")
        lines.append(
            f"- {group_id}: current {current_id}"
            + (f" (level {level})" if level is not None else "")
            + f" | weighted score {variant.get('score', 0):+.2f}"
        )
        for item in by_group.get(group_id, [])[:3]:
            lines.append(
                f"    option {item['law_id']} | level {item.get('level')} | "
                f"cost {item.get('cost')} | delta {item['delta']:+.2f} | "
                f"score {item['score']:+.2f}"
            )
    lines.append(
        "- change_law is executed by opening the politics view and clicking only "
        "screenshot-confirmed current-law and target icons; otherwise no click is sent."
    )
    return "\n".join(lines)


def prepare_law_task(
    task: dict, state: dict, pending: list | None = None
) -> tuple[list[dict], str]:
    """Validate one law task and return the bounded GUI action list."""
    if not isinstance(task, dict):
        return [], "unsupported task"
    kind = str(task.get("kind", "")).lower()
    if kind == "decision":
        return [], "decision execution is not implemented; use a national focus instead"
    if kind != "change_law":
        return [], "unsupported task kind"
    law_id = str(task.get("law_id", "")).strip()
    if not law_id:
        return [], "missing law id"
    group = _group_for(law_id)
    if group is None:
        return [], f"unknown law id: {law_id} is not in the installed law files"
    current = current_laws(state)
    current_id = current.get(group["id"])
    if not current_id:
        return [], (
            f"cannot identify the current {group['id']} law in the save; "
            "no screenshot anchor for the category row"
        )
    if current_id == law_id:
        return [], f"{law_id} is already in effect"
    source = group["variants"].get(current_id)
    target = group["variants"].get(law_id)
    if source is None or target is None:
        return [], f"law variant data for {group['id']} is incomplete"
    if not _allowed_for_player(target, str(state.get("player") or "")):
        return [], f"{law_id} is gated to another country in the installed files"
    if not _adjacent(source, target):
        return [], (
            f"{law_id} is not adjacent to the current {current_id}; "
            "the pilot only requests one-step law changes"
        )
    if any(
        isinstance(item, dict)
        and item.get("kind") == "change_law"
        and item.get("law_group") == group["id"]
        for item in (pending or [])
    ):
        return [], f"a {group['id']} law change is already awaiting save confirmation"
    actions = [{
        "do": "law_click",
        "law_id": law_id,
        "law_group": group["id"],
        "law_current": current_id,
    }]
    return actions, (
        f"validated: {current_id} -> {law_id} in {group['id']}; "
        "click is sent only after both icons are confirmed on screen"
    )


def verify_law_pending(pending: dict, state: dict) -> str:
    """Compare a pending law change with the newest save."""
    target = str(pending.get("law_id") or "")
    active = set(_active_ideas(state))
    if target and target in active:
        return "completed"
    baseline = pending.get("baseline") or {}
    signature = (
        state.get("path"),
        state.get("modified"),
        state.get("size"),
    )
    if baseline and signature != (
        baseline.get("path"),
        baseline.get("modified"),
        baseline.get("size"),
    ):
        return "failed"
    return "pending"
