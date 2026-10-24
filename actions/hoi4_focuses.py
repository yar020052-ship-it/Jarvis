"""National focus selection and GUI grounding for the HOI4 pilot.

The active focus tree comes from the campaign save (``focus_tree`` inside the
player's country scope).  Focus definitions are parsed from the installed mod
and base-game ``common/national_focus`` scripts, including shared focuses,
relative positioning, prerequisites and mutually exclusive branches.  The GUI
half of the module only emits a bounded sequence: open the focus view, then let
``actions.hoi4_focus_gui`` confirm the focus icon on a live screenshot before
clicking it.
"""

from __future__ import annotations

import re
from pathlib import Path

_CATALOG_CACHE: dict[tuple, dict] = {}
_FOCUS_DEF_CACHE: dict[int, dict] = {}
_LOC_CACHE: dict[str, dict[str, str]] = {}
_GUI_CACHE: dict[str, tuple[int, str]] = {}


def _roots() -> tuple[Path | None, Path | None]:
    from actions.hoi4_knowledge import discover_game_root, discover_mod_root

    return discover_mod_root(), discover_game_root()


def _source_roots() -> list[tuple[str, Path]]:
    mod_root, game_root = _roots()
    roots: list[tuple[str, Path]] = []
    if game_root:
        roots.append(("base", game_root))
    if mod_root:
        roots.append(("mod", mod_root))
    return roots


# ---------------------------------------------------------------------------
# Script parsing
# ---------------------------------------------------------------------------

def _scalar(text: str, key: str) -> str | None:
    match = re.search(
        rf"(?m)^\s*{re.escape(key)}\s*=\s*(?:\"([^\"]*)\"|([^\s#}}]+))", text
    )
    if not match:
        return None
    return (match.group(1) if match.group(1) is not None else match.group(2)).strip()


def _int_scalar(text: str, key: str, macros: dict[str, int] | None = None) -> int | None:
    raw = _scalar(text, key)
    if raw is None:
        return None
    raw = raw.strip().strip('"')
    if raw.startswith("@") and macros:
        return macros.get(raw[1:])
    try:
        return int(float(raw))
    except ValueError:
        return None


def _float_scalar(text: str, key: str) -> float | None:
    raw = _scalar(text, key)
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _matching_brace(text: str, open_at: int) -> int | None:
    from actions.hoi4_live_state import _matching_brace as matching

    return matching(text, open_at)


def _block(text: str, key: str) -> str:
    """Return the first direct ``key = { ... }`` body in *text*."""
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*\{{", text)
    if not match:
        return ""
    open_at = text.find("{", match.start(), match.end())
    close_at = _matching_brace(text, open_at)
    return text[open_at:close_at + 1] if close_at is not None else ""


def _blocks(text: str, key: str) -> list[str]:
    out = []
    for match in re.finditer(rf"(?m)^\s*{re.escape(key)}\s*=\s*\{{", text):
        open_at = text.find("{", match.start(), match.end())
        close_at = _matching_brace(text, open_at)
        if close_at is not None:
            out.append(text[open_at:close_at + 1])
    return out


def _focus_ids_in(block: str, key: str) -> list[str]:
    ids = []
    for body in _blocks(block, key):
        for match in re.finditer(r"\bfocus\s*=\s*([A-Za-z0-9_.-]+)", body):
            value = match.group(1)
            if value not in ids:
                ids.append(value)
    return ids


def _parse_focus(body: str, macros: dict[str, int]) -> dict | None:
    focus_id = _scalar(body, "id")
    if not focus_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", focus_id):
        return None
    x = _int_scalar(body, "x", macros)
    y = _int_scalar(body, "y", macros)
    reward = _block(body, "completion_reward")
    ai = _block(body, "ai_will_do")
    available = _block(body, "available")
    offsets = []
    for raw in _blocks(body, "offset"):
        ox = _int_scalar(raw, "x", macros)
        oy = _int_scalar(raw, "y", macros)
        trigger = _block(raw, "trigger")
        if ox is not None and oy is not None:
            offsets.append({"x": ox, "y": oy, "trigger": trigger})
    text = " ".join(
        part for part in (
            focus_id,
            _scalar(body, "icon") or "",
            reward,
        ) if part
    )
    return {
        "id": focus_id,
        "x": x,
        "y": y,
        "relative_position_id": _scalar(body, "relative_position_id"),
        "icon": _scalar(body, "icon") or "",
        "cost": _float_scalar(body, "cost") or 1.0,
        "prerequisites": _focus_ids_in(body, "prerequisite"),
        "mutually_exclusive": _focus_ids_in(body, "mutually_exclusive"),
        "available": bool(available),
        "available_text": available[:900],
        "ai_base": _float_scalar(ai, "base") or 0.0,
        "offsets": offsets,
        "effects": re.sub(r"\s+", " ", reward)[:1400],
        "search_text": re.sub(r"\s+", " ", text).lower(),
        "continuous": "continuous_focus" in body.lower() or "continuous = yes" in body.lower(),
    }


def _top_level_blocks(text: str, keys: str):
    from actions.hoi4_knowledge import _mask_comments

    clean = _mask_comments(text)
    for match in re.finditer(rf"(?m)^[ \t]*({keys})\s*=\s*\{{", clean):
        open_at = clean.find("{", match.start(), match.end())
        close_at = _matching_brace(clean, open_at)
        if close_at is None:
            continue
        yield match.group(1), text[open_at:close_at + 1]


def _parse_tree(block: str, macros: dict[str, int]) -> dict | None:
    from actions.hoi4_live_state import _direct_assignments

    tree_id = _scalar(block, "id")
    if not tree_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", tree_id):
        return None
    open_at = block.find("{")
    rows = _direct_assignments(block, open_at) if open_at >= 0 else []
    focuses: dict[str, dict] = {}
    shared: list[str] = []
    shortcuts: list[dict] = []
    initial: dict = {}
    for row in rows:
        key = row["key"]
        if key == "focus" and row.get("body"):
            parsed = _parse_focus(row["body"], macros)
            if parsed:
                focuses[parsed["id"]] = parsed
        elif key == "shared_focus":
            value = (row.get("value") or "").strip().strip('"')
            if value and re.fullmatch(r"[A-Za-z0-9_.-]+", value) and value not in shared:
                shared.append(value)
        elif key == "shortcut" and row.get("body"):
            target = _scalar(row["body"], "target")
            name = _scalar(row["body"], "name")
            if target and name:
                shortcuts.append({"name": name, "target": target})
    initial_raw = _block(block, "initial_show_position")
    if initial_raw:
        initial = {
            "focus": _scalar(initial_raw, "focus"),
            "x": _int_scalar(initial_raw, "x", macros),
            "y": _int_scalar(initial_raw, "y", macros),
        }
    return {
        "id": tree_id,
        "focuses": focuses,
        "shared": shared,
        "shortcuts": shortcuts,
        "initial": initial,
        "default": (_scalar(block, "default") or "").lower() == "yes",
    }


def _catalog() -> dict:
    roots = _source_roots()
    signature = []
    for label, root in roots:
        folder = root / "common" / "national_focus"
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.txt")):
            try:
                stat = path.stat()
            except OSError:
                continue
            signature.append((label, str(path), stat.st_size, stat.st_mtime_ns))
    cache_key = tuple(signature)
    cached = _CATALOG_CACHE.get(cache_key)
    if cached is not None:
        return cached

    trees: dict[str, dict] = {}
    shared: dict[str, dict] = {}
    sources: dict[str, str] = {}
    # Base game first, then Millennium Dawn overrides.
    for label, root in roots:
        folder = root / "common" / "national_focus"
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            macros = {
                name: int(value)
                for name, value in re.findall(r"(?m)^\s*@([A-Za-z0-9_]+)\s*=\s*(-?\d+)\s*$", text)
            }
            for kind, block in _top_level_blocks(text, "focus_tree|shared_focus"):
                if kind == "focus_tree":
                    tree = _parse_tree(block, macros)
                    if tree:
                        trees[tree["id"]] = tree
                        sources[tree["id"]] = f"{label}/{path.name}"
                else:
                    parsed = _parse_focus(block, macros)
                    if parsed:
                        shared[parsed["id"]] = parsed
    data = {"trees": trees, "shared": shared, "sources": sources}
    _CATALOG_CACHE.clear()
    _CATALOG_CACHE[cache_key] = data
    _FOCUS_DEF_CACHE.clear()
    return data


def _tree_definitions(tree: dict) -> dict[str, dict]:
    """Resolve inline and shared focus definitions for one tree."""
    catalog = _catalog()
    definitions: dict[str, dict] = {}
    for focus_id in tree.get("shared", []):
        item = catalog["shared"].get(focus_id)
        if item:
            definitions[focus_id] = item
    definitions.update(tree.get("focuses", {}))
    return definitions


def _grid_positions(tree: dict, definitions: dict[str, dict]) -> dict[str, tuple[int, int]]:
    resolved: dict[str, tuple[int, int]] = {}

    def resolve(focus_id: str, stack: frozenset[str]) -> tuple[int, int] | None:
        if focus_id in resolved:
            return resolved[focus_id]
        if focus_id in stack:
            return None
        item = definitions.get(focus_id)
        if not item or item.get("x") is None or item.get("y") is None:
            return None
        x, y = item["x"], item["y"]
        parent_id = item.get("relative_position_id")
        if parent_id and parent_id in definitions:
            parent = resolve(parent_id, stack | {focus_id})
            if parent is None:
                return None
            x += parent[0]
            y += parent[1]
        for offset in item.get("offsets", []):
            trigger = offset.get("trigger", "")
            if trigger and "has_focus_tree" in trigger:
                if tree["id"] not in trigger:
                    continue
            x += offset["x"]
            y += offset["y"]
        resolved[focus_id] = (x, y)
        return resolved[focus_id]

    for focus_id in definitions:
        resolve(focus_id, frozenset())
    return resolved


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

_POSITIVE = (
    ("industrial_capacity", 11), ("factory", 8), ("construction_speed", 9),
    ("production", 7), ("production_speed", 7), ("research_speed", 10),
    ("research", 5), ("technology", 4), ("infrastructure", 7),
    ("stability", 6), ("war_support", 5), ("political_power", 7),
    ("economy", 7), ("economic", 7), ("budget", 6), ("tax", 5), ("gdp", 6),
    ("inflation", 5), ("debt", 4), ("resource", 5), ("oil", 5), ("fuel", 5),
    ("energy", 5), ("manpower", 5), ("recruitable", 5), ("division", 5),
    ("army", 4), ("equipment", 4), ("supply", 4), ("logistics", 4),
    ("air", 3), ("navy", 3), ("fort", 3), ("doctrine", 4),
    ("centralization", 3), ("efficiency", 6), ("productivity", 6),
)
_NEGATIVE = (
    ("declare_war", 14), ("war_goal", 12), ("wargoal", 12), ("justify", 10),
    ("puppet", 9), ("annex", 9), ("civil_war", 14), ("create_faction", 5),
    ("join_faction", 4), ("guarantee", 3), ("lend_lease", 3),
    ("volunteer", 3), ("ban", 4), ("purge", 6),
)


def _score_focus(item: dict) -> float:
    text = item.get("search_text", "") + " " + item.get("effects", "").lower()
    score = 0.0
    for needle, weight in _POSITIVE:
        if needle in text:
            score += weight
    for needle, weight in _NEGATIVE:
        if needle in text:
            score -= weight
    score += max(0.0, min(5.0, item.get("ai_base", 0.0))) * 0.6
    if item.get("available"):
        score -= 1.5
    y = item.get("grid_y", 0)
    if y > 10:
        score -= (y - 10) * 0.15
    return score


def active_tree(state: dict) -> dict | None:
    catalog = _catalog()
    focuses = state.get("focuses") or {}
    tree_id = focuses.get("tree")
    if tree_id and tree_id in catalog["trees"]:
        return catalog["trees"][tree_id]
    fallback = next((tree for tree in catalog["trees"].values() if tree.get("default")), None)
    return fallback


def focus_candidates(state: dict, pending=None, limit: int = 60) -> list[dict]:
    tree = active_tree(state)
    if not tree:
        return []
    definitions = _tree_definitions(tree)
    positions = _grid_positions(tree, definitions)
    focuses = state.get("focuses") or {}
    completed = set(focuses.get("completed", []))
    current = focuses.get("current")
    pending_ids = set()
    if isinstance(pending, dict):
        pending = [pending]
    for item in pending or []:
        if isinstance(item, dict) and item.get("kind") == "start_focus":
            pending_ids.add(str(item.get("focus_id", "")))
    out = []
    for focus_id, item in definitions.items():
        if item.get("continuous") or focus_id in completed or focus_id == current:
            continue
        if focus_id in pending_ids:
            continue
        position = positions.get(focus_id)
        if position is None:
            continue
        groups = item.get("prerequisites") or []
        if groups:
            if not any(all(parent in completed for parent in group) for group in groups):
                continue
        if any(other in completed or other == current
               for other in item.get("mutually_exclusive", [])):
            continue
        candidate = {
            **item,
            "grid_x": position[0],
            "grid_y": position[1],
            "name": focus_name(focus_id),
            "gated": bool(item.get("available")),
        }
        candidate["score"] = _score_focus(candidate)
        out.append(candidate)
    out.sort(key=lambda item: (item["gated"], -item["score"], item["grid_y"], item["grid_x"], item["id"]))
    return out[:max(1, limit)]


def available_focuses(state: dict) -> list[str]:
    return [item["id"] for item in focus_candidates(state, limit=40)]


def best_focus_task(state: dict, pending=None) -> dict | None:
    focuses = state.get("focuses") or {}
    if focuses.get("current"):
        return None
    candidates = focus_candidates(state, pending)
    if not candidates:
        return None
    target = candidates[0]
    return {
        "kind": "start_focus",
        "focus_id": target["id"],
        "reason": f"best available focus by parsed rewards: {target['effects'][:180]}",
    }


def focus_task_context(state: dict, pending=None, limit: int = 12) -> str:
    tree = active_tree(state)
    focuses = state.get("focuses") or {}
    if not tree:
        return (
            "FILE-GROUNDED FOCUS STATE:\n"
            "- Active national focus tree is unknown (save has not been parsed yet).\n"
            "- Do not send start_focus tasks until the active tree is known."
        )
    completed = set(focuses.get("completed", []))
    current = focuses.get("current")
    candidates = focus_candidates(state, pending, limit=max(limit, 20))
    lines = [
        "FILE-GROUNDED FOCUS STATE:",
        f"- Active focus tree: {tree['id']} ({_catalog()['sources'].get(tree['id'], '?')}).",
        f"- Completed focuses: {len(completed)}; current focus: {current or 'none'}.",
        f"- Available focus options with parsed prerequisites/grid positions: {len(candidates)}.",
    ]
    if current:
        lines.append("- A focus is already in progress; do not start another one.")
    for item in candidates[:max(1, min(limit, 20))]:
        name = item.get("name") or item["id"]
        gate = "conditional" if item.get("gated") else "open"
        lines.append(
            f"  - {item['id']} | {name} | grid {item['grid_x']},{item['grid_y']} | "
            f"cost {item['cost']:g} | {gate} | score {item['score']:.1f} | "
            f"effects {item['effects'][:180]}"
        )
    lines.append(
        "- start_focus is executed by opening the focus tree and clicking only a "
        "screenshot-confirmed matching focus icon. If the icon cannot be found, "
        "no click is sent."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Localisation
# ---------------------------------------------------------------------------

def _language() -> str:
    try:
        from actions.hoi4_live_state import _game_data_dir

        text = (_game_data_dir() / "settings.txt").read_text(encoding="utf-8-sig", errors="replace")
        match = re.search(r'(?m)^\s*language\s*=\s*"([^"]+)"', text)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return "l_english"


def _localisation(language: str) -> dict[str, str]:
    cached = _LOC_CACHE.get(language)
    if cached is not None:
        return cached
    result: dict[str, str] = {}
    for _label, root in _source_roots():
        folder = root / "localisation"
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob(f"*_{language}.yml")):
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                match = re.match(r'\s*([\w.-]+):\d*\s+"(.*?)"\s*$', line)
                if not match:
                    continue
                result.setdefault(match.group(1), match.group(2).replace("\\n", " "))
    _LOC_CACHE[language] = result
    return result


def focus_name(focus_id: str) -> str:
    language = _language()
    for candidate in (language, "l_english"):
        table = _localisation(candidate)
        if focus_id in table:
            return table[focus_id]
    return focus_id


# ---------------------------------------------------------------------------
# GUI grounding
# ---------------------------------------------------------------------------

def focus_view_gui() -> str:
    mod_root, game_root = _roots()
    for root in (mod_root, game_root):
        if not root:
            continue
        path = root / "interface" / "nationalfocusview.gui"
        try:
            stat = path.stat()
        except OSError:
            continue
        cached = _GUI_CACHE.get(str(path))
        if cached and cached[0] == stat.st_mtime_ns:
            return cached[1]
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        _GUI_CACHE[str(path)] = (stat.st_mtime_ns, text)
        return text
    return ""


def _gui_constant(gui: str, name: str) -> tuple[float, float] | None:
    match = re.search(
        rf"(?is)positionType\s*=\s*\{{[^}}]*name\s*=\s*\"?{re.escape(name)}\"?[^}}]*"
        rf"position\s*=\s*\{{\s*x\s*=\s*(-?[\d.]+)\s*y\s*=\s*(-?[\d.]+)",
        gui,
    )
    return (float(match.group(1)), float(match.group(2))) if match else None


def focus_view_geometry(screen_size: tuple[int, int] | None = None) -> dict:
    gui = focus_view_gui()
    spacing = _gui_constant(gui, "focus_spacing") or (96.0, 130.0)
    center = _gui_constant(gui, "national_focus_center") or (130.0, 32.0)
    window = re.search(
        r"(?is)containerWindowType\s*=\s*\{[^}]*?name\s*=\s*\"nationalfocusview\""
        r"[^}]*?position\s*=\s*\{\s*x\s*=\s*(-?[\d.]+)\s*y\s*=\s*(-?[\d.]+)",
        gui,
    )
    find = re.search(
        r"(?is)containerWindowType\s*=\s*\{[^}]*?name\s*=\s*\"find_view\""
        r"[^}]*?position\s*=\s*\{\s*x\s*=\s*(-?[\d.]+)\s*y\s*=\s*(-?[\d.]+)"
        r"[^}]*?size\s*=\s*\{\s*width\s*=\s*(\d+)\s*height\s*=\s*(\d+)",
        gui,
    )
    edit = re.search(
        r"(?is)editBoxType\s*=\s*\{[^}]*?name\s*=\s*\"to_find_text\""
        r"[^}]*?position\s*=\s*\{\s*x\s*=\s*(-?[\d.]+)\s*y\s*=\s*(-?[\d.]+)"
        r"[^}]*?size\s*=\s*\{\s*(?:width|x)\s*=\s*(\d+)\s*(?:height|y)\s*=\s*(\d+)",
        gui,
    )
    try:
        from actions.hoi4_live_state import _game_data_dir

        settings = (_game_data_dir() / "settings.txt").read_text(encoding="utf-8-sig", errors="replace")
        size = re.search(r"(?m)^\s*size\s*=\s*\{\s*x\s*=\s*(\d+)\s*y\s*=\s*(\d+)", settings)
        scale_match = re.search(r"(?m)^\s*gui_scale\s*=\s*([\d.]+)", settings)
        game_size = (int(size.group(1)), int(size.group(2))) if size else (1920, 1200)
        gui_scale = float(scale_match.group(1)) if scale_match else 1.0
    except Exception:
        game_size, gui_scale = (1920, 1200), 1.0
    screen = screen_size or game_size
    window_x = float(window.group(1)) if window else -3.0
    window_y = float(window.group(2)) if window else 78.0
    # find_view is anchored to the upper-right corner of the focus view.
    find_x, find_y = (float(find.group(1)), float(find.group(2))) if find else (-344.0, 8.0)
    find_w = float(find.group(3)) if find else 385.0
    edit_x, edit_y = (float(edit.group(1)), float(edit.group(2))) if edit else (41.0, 14.0)
    edit_w = float(edit.group(3)) if edit else 150.0
    edit_h = float(edit.group(4)) if edit else 25.0
    ratio_x = screen[0] / game_size[0] if game_size[0] else 1.0
    ratio_y = screen[1] / game_size[1] if game_size[1] else 1.0
    right_edge = (window_x + game_size[0]) * gui_scale
    search_x = (right_edge + find_x + edit_x + edit_w / 2) * ratio_x
    search_y = (window_y + find_y + edit_y + edit_h / 2) * gui_scale * ratio_y
    return {
        "spacing": (spacing[0] * gui_scale, spacing[1] * gui_scale),
        "center": (center[0] * gui_scale, center[1] * gui_scale),
        "search_click": (int(max(0, min(screen[0] - 1, search_x))),
                         int(max(0, min(screen[1] - 1, search_y)))),
        "game_size": game_size,
        "screen_size": screen,
        "gui_scale": gui_scale,
    }


# ---------------------------------------------------------------------------
# Politics view -> national focus tree
# ---------------------------------------------------------------------------

_GUI_TOKEN = re.compile(
    r"(?m)^\s*(guiTypes|containerWindowType|buttonType|windowType|iconType)\s*=\s*\{"
)


def _gui_paths(name: str) -> list[Path]:
    mod_root, game_root = _roots()
    paths = []
    for root in (mod_root, game_root):
        if not root:
            continue
        path = root / "interface" / f"{name}.gui"
        if path.is_file():
            paths.append(path)
    return paths


def _gui_nodes(text: str) -> list[dict]:
    nodes: list[dict] = []
    for match in _GUI_TOKEN.finditer(text):
        start = text.find("{", match.start(), match.end())
        end = _matching_brace(text, start)
        if end is None:
            continue
        block = text[start:end + 1]
        pos = re.search(
            r"(?i)\bposition\s*=\s*\{\s*x\s*=\s*(-?[\d.]+)\s*y\s*=\s*(-?[\d.]+)", block
        )
        show = re.search(
            r"(?i)\bshow_position\s*=\s*\{\s*x\s*=\s*(-?[\d.]+)\s*y\s*=\s*(-?[\d.]+)", block
        )
        nodes.append({
            "kind": match.group(1),
            "name": _scalar(block, "name"),
            "start": match.start(),
            "end": end,
            "position": (float(pos.group(1)), float(pos.group(2))) if pos else None,
            "show_position": (float(show.group(1)), float(show.group(2))) if show else None,
            "orientation": (_scalar(block, "Orientation") or _scalar(block, "orientation") or "UPPER_LEFT").upper(),
        })
    return nodes


def _screen_geometry() -> tuple[int, int, float]:
    try:
        from actions.hoi4_live_state import _game_data_dir

        settings = (_game_data_dir() / "settings.txt").read_text(encoding="utf-8-sig", errors="replace")
        size = re.search(r"(?m)^\s*size\s*=\s*\{\s*x\s*=\s*(\d+)\s*y\s*=\s*(\d+)", settings)
        scale_match = re.search(r"(?m)^\s*gui_scale\s*=\s*([\d.]+)", settings)
        game_size = (int(size.group(1)), int(size.group(2))) if size else (1920, 1200)
        gui_scale = float(scale_match.group(1)) if scale_match else 1.0
    except Exception:
        game_size, gui_scale = (1920, 1200), 1.0
    return game_size[0], game_size[1], gui_scale


def named_button_position(button: str, gui_name: str = "countrypoliticsview") -> tuple[int, int] | None:
    """Resolve a named button in a specific window to normalized 0-1000 coords.

    HOI4 windows can be off-screen while hidden; the top-level ``show_position``
    is the position used after the open animation, so it is preferred for the
    outermost window.  Returns ``None`` when the geometry cannot be grounded.
    """
    for path in _gui_paths(gui_name):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        nodes = _gui_nodes(text)
        target = next(
            (node for node in nodes if node["kind"] == "buttonType" and node["name"] == button),
            None,
        )
        if target is None or target["position"] is None:
            continue
        ancestors = [
            node for node in nodes
            if node["kind"] in {"containerWindowType", "windowType"}
            and node["start"] < target["start"] and node["end"] > target["end"]
        ]
        ancestors.sort(key=lambda node: node["start"])
        width, height, gui_scale = _screen_geometry()
        x = y = 0.0
        for index, parent in enumerate(ancestors):
            point = parent["show_position"] if index == 0 and parent["show_position"] else parent["position"]
            if not point:
                continue
            px, py = point
            if "RIGHT" in parent["orientation"]:
                x = width + x + px
            else:
                x += px
            if "LOWER" in parent["orientation"]:
                y = height + y + py
            else:
                y += py
        bx, by = target["position"]
        if "RIGHT" in target["orientation"]:
            x = width + x + bx
        else:
            x += bx
        if "LOWER" in target["orientation"]:
            y = height + y + by
        else:
            y += by
        # Small sprite: aim at its centre rather than the top-left corner.
        px = int((x + 17) * gui_scale)
        py = int((y + 17) * gui_scale)
        if not (0 <= px < width and 0 <= py < height):
            continue
        return round(px / width * 1000), round(py / height * 1000)
    return None


def focus_open_actions() -> list[dict]:
    """Keyboard/mouse steps that open the national-focus view from the map."""
    return [
        {"do": "press", "key": "q"},
        {"do": "wait", "seconds": 0.9},
        {"do": "click_named", "name": "add_national_goal_button", "gui": "countrypoliticsview"},
        {"do": "wait", "seconds": 0.9},
    ]


# ---------------------------------------------------------------------------
# Typed tasks
# ---------------------------------------------------------------------------

def prepare_focus_task(task: dict, state: dict, pending=None) -> tuple[list[dict], str]:
    if not isinstance(task, dict) or str(task.get("kind", "")).lower() != "start_focus":
        return [], "unsupported task kind"
    focus_id = str(task.get("focus_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", focus_id):
        return [], "invalid focus id"
    focuses = state.get("focuses") or {}
    if focuses.get("current"):
        return [], f"precondition failed: focus {focuses['current']} is already in progress"
    if focus_id in set(focuses.get("completed", [])):
        return [], "precondition failed: focus already completed"
    candidates = {item["id"]: item for item in focus_candidates(state, pending, limit=400)}
    target = candidates.get(focus_id)
    if not target:
        return [], "precondition failed: focus is unavailable, hidden, or its prerequisites are not met"
    tree = active_tree(state)
    if not tree:
        return [], "precondition failed: active focus tree is unknown"
    actions = focus_open_actions()
    actions.append({"do": "focus_click", "focus_id": focus_id, "tree_id": tree["id"]})
    if named_button_position("add_national_goal_button", "countrypoliticsview") is None:
        return [], "cannot safely open focus view: add_national_goal_button geometry not found"
    return actions, (
        f"validated: {focus_id} is available in {tree['id']}; "
        "click is sent only after the icon is confirmed on screen"
    )


def verify_focus_pending(pending: dict | None, state: dict) -> str:
    """Return waiting/completed/failed for a pending focus choice.

    A focus is considered successfully started as soon as a newer save shows it
    as the current focus (or completed).  Focus completion can take many weeks,
    so waiting for the full reward would block the pilot for a long time.
    """
    if not pending:
        return "none"
    baseline = pending.get("baseline", {}) or {}
    if (state.get("path"), state.get("modified"), state.get("size")) == (
            baseline.get("path"), baseline.get("modified"), baseline.get("size")):
        return "waiting"
    focus_id = str(pending.get("focus_id", ""))
    focuses = state.get("focuses") or {}
    if focus_id in set(focuses.get("completed", [])):
        return "completed"
    if focuses.get("current") == focus_id:
        return "completed"
    return "failed"
