"""Typed, file-grounded tasks for the HOI4 Millennium Dawn pilot.

The first executable task is selecting an eligible technology in any category
whose tab, grid and icon can be grounded in the installed GUI. Its preconditions
come from the live text save and installed technology scripts. The save stays
read-only, and the task is complete only after a later save shows the selected
technology in an active research slot.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path


_CATALOG_CACHE: dict[tuple, dict[str, dict]] = {}
_SPRITE_SIZE_CACHE: dict[str, tuple[float, float] | None] = {}
_GUI_TEXT_CACHE: dict[str, tuple[int, str]] = {}
_GUI_NODES_CACHE: dict[int, list[dict]] = {}
_FOLDER_LAYOUT_CACHE: dict[tuple[int, str, int, int], dict | None] = {}


def _roots() -> tuple[Path | None, Path | None]:
    from actions.hoi4_knowledge import discover_game_root, discover_mod_root

    return discover_mod_root(), discover_game_root()


def _catalog() -> dict[str, dict]:
    from actions.hoi4_live_state import _block_start, _direct_assignments, _matching_brace
    from actions.hoi4_knowledge import _mask_comments

    mod_root, game_root = _roots()
    if not mod_root:
        return {}
    roots = [("mod", mod_root)]
    if game_root and game_root.resolve() != mod_root.resolve():
        roots.append(("base", game_root))
    signature = []
    for label, root in roots:
        for path in sorted((root / "common" / "technologies").rglob("*.txt")):
            try:
                stat = path.stat()
                signature.append((label, str(path), stat.st_size, stat.st_mtime_ns))
            except OSError:
                continue
    cache_key = tuple(signature)
    cached = _CATALOG_CACHE.get(cache_key)
    if cached is not None:
        return cached

    catalog: dict[str, dict] = {}
    # Millennium Dawn overrides win over base-game definitions.
    for label, root in reversed(roots):
        folder = root / "common" / "technologies"
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            clean = _mask_comments(text)
            technologies_at = _block_start(clean, "technologies")
            if technologies_at is None:
                continue
            technologies_end = _matching_brace(clean, technologies_at)
            if technologies_end is None:
                continue
            macros = {
                name: int(value)
                for name, value in re.findall(
                    r"(?m)^\s*@([A-Za-z0-9_]+)\s*=\s*(-?\d+)\s*$",
                    clean[technologies_at:technologies_end + 1],
                )
            }
            for row in _direct_assignments(clean, technologies_at):
                tech_id = row["key"]
                if not row.get("body") or tech_id.startswith("@"):  # skip scalar macros
                    continue
                tech = row["body"]
                folder_at = _block_start(tech, "folder")
                folder_body = ""
                if folder_at is not None:
                    folder_end = _matching_brace(tech, folder_at)
                    if folder_end is not None:
                        folder_body = tech[folder_at:folder_end + 1]
                folder_match = re.search(r"\bname\s*=\s*([A-Za-z0-9_.-]+)", folder_body)
                folder_name = folder_match.group(1) if folder_match else None
                position_match = re.search(
                    r"(?s)\bposition\s*=\s*\{\s*x\s*=\s*([^\s}]+)\s+y\s*=\s*([^\s}]+)",
                    folder_body,
                )
                x = _resolve_coordinate(position_match.group(1), macros) if position_match else None
                y = _resolve_coordinate(position_match.group(2), macros) if position_match else None
                year = _int_scalar(tech, "start_year")
                research_cost = _float_scalar(tech, "research_cost") or 1.0
                outgoing = re.findall(r"\bleads_to_tech\s*=\s*([A-Za-z0-9_.-]+)", tech)
                allow_at = _block_start(tech, "allow")
                allow = allow_at is not None
                effect_lines = []
                for line in tech.splitlines():
                    item = line.strip()
                    if not item or item.startswith((
                        "#", "path =", "folder =", "categories =", "ai_will_do =",
                        "name =", "position =", "allow =", "allow_branch =",
                    )):
                        continue
                    if item.startswith(("start_year =", "research_cost =", "special_project_specialization =")):
                        continue
                    effect_lines.append(item)
                    if len(effect_lines) >= 4:
                        break
                definition = {
                    "id": tech_id,
                    "source": f"{label}/{path.relative_to(root).as_posix()}",
                    "folder": folder_name,
                    "x": x,
                    "y": y,
                    "year": year,
                    "cost": research_cost,
                    "gated": allow,
                    "leads_to": outgoing,
                    "effects": " ".join(effect_lines)[:260],
                }
                if label == "mod":
                    catalog[tech_id] = definition
                else:
                    catalog.setdefault(tech_id, definition)
    incoming: dict[str, set[str]] = {}
    for tech_id, item in catalog.items():
        for child in item.get("leads_to", []):
            incoming.setdefault(child, set()).add(tech_id)
    for tech_id, item in catalog.items():
        item["prerequisites"] = sorted(incoming.get(tech_id, set()))
    _CATALOG_CACHE.clear()
    _CATALOG_CACHE[cache_key] = catalog
    return catalog


def _scalar(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*(?:\"([^\"]*)\"|([^\s#}}]+))", text)
    if not match:
        return None
    return (match.group(1) if match.group(1) is not None else match.group(2)).strip()


def _int_scalar(text: str, key: str) -> int | None:
    raw = _scalar(text, key)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _float_scalar(text: str, key: str) -> float | None:
    raw = _scalar(text, key)
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _resolve_coordinate(raw: str, macros: dict[str, int]) -> int | None:
    value = raw.strip().strip('"')
    if value.startswith("@"):
        return macros.get(value[1:])
    try:
        return int(value)
    except ValueError:
        return None


def _pending_ids(pending) -> set[str]:
    if isinstance(pending, dict):
        rows = [pending]
    elif isinstance(pending, (list, tuple, set)):
        rows = list(pending)
    else:
        rows = [pending]
    if isinstance(pending, str):
        rows = [pending]
    ids = set()
    for row in rows:
        value = row.get("technology_id", "") if isinstance(row, dict) else row
        if value:
            ids.add(str(value))
    return ids


def _eligible_candidates(state: dict, pending=None) -> list[dict]:
    research = state.get("research", {})
    completed = set(research.get("completed", []))
    active = set(research.get("active", []))
    pending_ids = _pending_ids(pending)
    year_match = re.match(r"^(\d{4})", str(state.get("date", "")))
    current_year = int(year_match.group(1)) if year_match else 0
    catalog = _catalog()
    gui, gui_width, gui_height = _technology_gui()
    if not gui:
        return []
    candidates = []
    for item in catalog.values():
        tech_id = item["id"]
        if (not item.get("folder") or item.get("x") is None
                or item.get("y") is None or item.get("year") is None):
            continue
        if item.get("gated") or item["year"] > current_year:
            continue
        if tech_id in completed or tech_id in active or tech_id in pending_ids:
            continue
        # A visible incoming technology path is treated as a prerequisite.
        # This avoids selecting a later stage before its predecessor.
        if any(parent not in completed for parent in item.get("prerequisites", [])):
            continue
        layout = _folder_layout(gui, item["folder"], gui_width, gui_height)
        if not layout:
            continue
        x, y = _tech_pixel_position(item, layout, gui_width, gui_height)
        if x is None or y is None:
            continue
        candidates.append({**item, "pixel_x": x, "pixel_y": y,
                           "grid_width": layout["grid_width"],
                           "grid_height": layout["grid_height"]})
    def score(item: dict) -> tuple:
        description = f"{item['id']} {item['effects']}".lower()
        modifiers = 0.0
        for modifier, raw_value in re.findall(
            r"\b(research_speed_factor|industrial_capacity_factory|"
            r"production_factory_max_efficiency_factor|production_speed|"
            r"construction_speed_factor|global_building_slots_factor)\s*=\s*(-?\d+(?:\.\d+)?)",
            item["effects"],
        ):
            weight = {
                "research_speed_factor": 1200,
                "industrial_capacity_factory": 450,
                "production_factory_max_efficiency_factor": 350,
                "production_speed": 300,
                "construction_speed_factor": 300,
                "global_building_slots_factor": 280,
            }[modifier]
            modifiers += float(raw_value) * weight
        strategic = sum(
            weight for needle, weight in (
                ("industrial_capacity_factory", 9), ("research_speed", 5),
                ("construction_speed", 6), ("production_efficiency", 6),
                ("factory", 4), ("productivity", 4), ("infrastructure", 3),
                ("logistics", 3), ("supply", 3), ("infantry_weapons", 2),
                ("artillery", 2), ("armor", 2), ("air_defense", 2),
                ("nanofibers", 2), ("printing", 2),
            ) if needle in description
        )
        return (modifiers + strategic, item["year"], -item["cost"], item["id"])
    candidates.sort(key=score, reverse=True)
    return candidates


def research_task_context(state: dict, pending: dict | None = None, limit: int = 24) -> str:
    research = state.get("research", {})
    pending_ids = sorted(_pending_ids(pending))
    candidates = _eligible_candidates(state, pending_ids)
    active = research.get("active", [])
    lines = [
        "FILE-GROUNDED RESEARCH TASK STATE:",
        f"- Empty slots: {research.get('empty_slots', 0)} of {len(research.get('slots', []))}.",
        f"- Active technology IDs: {', '.join(active[:8]) or 'none'}.",
        f"- Installed, date-valid technology options with known category tabs, grid geometry and GUI targets: {len(candidates)}.",
    ]
    if pending_ids:
        lines.append(f"- Selections awaiting save verification: {', '.join(pending_ids[:8])}; do not select them again yet.")
    lines.append("- Candidate records (IDs, category, dates, prerequisites, cost and effects parsed from installed mod files):")
    for item in candidates[:max(1, min(limit, 40))]:
        lines.append(
            f"  - {item['id']} | category {item['folder']} | year {item['year']} | cost {item['cost']:g} | "
            f"prerequisites={','.join(item['prerequisites']) or 'none'} | effects={item['effects']}"
        )
    return "\n".join(lines)


def best_research_task(state: dict, pending: dict | None = None) -> dict | None:
    """Choose the strongest file-grounded research option as a planner fallback."""
    research = state.get("research", {})
    pending_ids = _pending_ids(pending)
    if int(research.get("empty_slots", 0) or 0) - len(pending_ids) < 1:
        return None
    candidates = _eligible_candidates(state, pending_ids)
    if not candidates:
        return None
    target = candidates[0]
    return {
        "kind": "start_research",
        "technology_id": target["id"],
        "reason": f"best eligible installed technology by parsed effects: {target['effects'][:160]}",
    }


def prepare_task(task: dict, state: dict, pending: dict | None = None,
                 *, open_technology_view: bool = True) -> tuple[list[dict], str]:
    """Validate a typed research task and translate it to file-grounded UI input."""
    if not isinstance(task, dict) or str(task.get("kind", "")).lower() != "start_research":
        return [], "unsupported task kind"
    tech_id = str(task.get("technology_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", tech_id):
        return [], "invalid technology id"
    if int(state.get("research", {}).get("empty_slots", 0) or 0) < 1:
        return [], "precondition failed: no empty research slot in the save"
    candidates = {item["id"]: item for item in _eligible_candidates(state, pending)}
    target = candidates.get(tech_id)
    if not target:
        return [], "precondition failed: technology is completed, gated, unavailable, unsupported or not in the candidate set"
    try:
        from actions.hoi4_live_state import _game_data_dir

        docs = _game_data_dir()
        settings = (docs / "settings.txt").read_text(encoding="utf-8-sig", errors="replace") if docs else ""
        resolution = re.search(r"(?m)^\s*size\s*=\s*\{\s*x\s*=\s*(\d+)\s*y\s*=\s*(\d+)", settings)
        if not resolution:
            return [], "cannot ground GUI coordinates: HOI4 resolution is missing"
        width, height = int(resolution.group(1)), int(resolution.group(2))
        from actions.game_pilot import _pyautogui

        screen_w, screen_h = _pyautogui().size()
        if (screen_w, screen_h) != (width, height):
            return [], f"cannot safely click: display {screen_w}x{screen_h} differs from HOI4 GUI {width}x{height}"
    except Exception as exc:
        return [], f"cannot safely read display geometry: {exc}"

    mod_root, game_root = _roots()
    gui, gui_width, gui_height = _technology_gui()
    if not gui:
        return [], "installed technology-tree GUI was not found"
    if (gui_width, gui_height) != (width, height):
        return [], "settings changed while preparing the task; refresh game geometry"
    layout = _folder_layout(gui, target["folder"], width, height)
    if not layout:
        return [], f"technology category {target['folder']} has no grounded tab or item template"
    tab = layout["tab"]
    research_button = _named_node_position(gui, "research", "buttonType", width, height)
    if not tab or not research_button:
        return [], "required technology-category tab or confirmation button is missing from the installed GUI"
    px, py = target["pixel_x"], target["pixel_y"]
    if not (0 <= px < width and 0 <= py < height):
        return [], "technology lies outside the current GUI viewport; scrolling has not been grounded"

    actions = []
    if open_technology_view:
        # W is the technology-button shortcut verified from this install's
        # topbar.gui. Do not press Escape: it can close a window the owner is
        # currently using while both of them play.
        actions.append({"do": "press", "key": "w"})
    actions.extend([
        {"do": "wait", "seconds": 0.35},
        {"do": "click", "x": _norm(tab[0] + tab[2] / 2, width), "y": _norm(tab[1] + tab[3] / 2, height)},
        {"do": "wait", "seconds": 0.35},
        {"do": "click", "x": _norm(px, width), "y": _norm(py, height)},
        {"do": "wait", "seconds": 0.35},
        {"do": "click", "x": _norm(research_button[0] + research_button[2] / 2, width),
         "y": _norm(research_button[1] + research_button[3] / 2, height)},
    ])
    return actions, f"validated: empty slot, installed {target['folder']} technology, visible GUI target and research button"


def verify_pending(pending: dict | None, state: dict) -> str:
    """Return waiting, completed or failed after a newer save arrives."""
    if not pending:
        return "none"
    baseline = pending.get("baseline", {}) or {}
    if (state.get("path"), state.get("modified"), state.get("size")) == (
            baseline.get("path"), baseline.get("modified"), baseline.get("size")):
        return "waiting"
    tech_id = str(pending.get("technology_id", ""))
    research = state.get("research", {}) or {}
    if tech_id in research.get("active", []) or tech_id in research.get("completed", []):
        return "completed"
    # Сохранение обновилось, но технология ещё не отражена в active/completed.
    # Если число свободных слотов уменьшилось относительно момента клика —
    # слот занят выбранным исследованием, клик отработал. Ждём следующего
    # сохранения, чтобы оно отобразилось в active/completed.
    base_slots = baseline.get("empty_slots")
    cur_slots = research.get("empty_slots")
    try:
        if base_slots is not None and cur_slots is not None:
            if int(cur_slots) < int(base_slots):
                return "completed"
    except (TypeError, ValueError):
        pass
    return "failed"


def _technology_gui() -> tuple[str, int, int]:
    """Return the active installed technology GUI and its configured screen size."""
    from actions.hoi4_live_state import _game_data_dir
    from actions.hoi4_knowledge import discover_game_root, discover_mod_root

    width, height = 1920, 1200
    docs = _game_data_dir()
    try:
        settings = (docs / "settings.txt").read_text(encoding="utf-8-sig", errors="replace") if docs else ""
        size = re.search(r"(?m)^\s*size\s*=\s*\{\s*x\s*=\s*(\d+)\s*y\s*=\s*(\d+)", settings)
        if size:
            width, height = int(size.group(1)), int(size.group(2))
    except OSError:
        pass
    mod_root, game_root = discover_mod_root(), discover_game_root()
    roots = [root for root in (mod_root, game_root) if root]
    for root in roots:
        path = root / "interface" / "countrytechtreeview.gui"
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        cached = _GUI_TEXT_CACHE.get(str(path))
        if cached and cached[0] == modified:
            return cached[1], width, height
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        _GUI_TEXT_CACHE.clear()
        _GUI_NODES_CACHE.clear()
        _FOLDER_LAYOUT_CACHE.clear()
        _GUI_TEXT_CACHE[str(path)] = (modified, text)
        return text, width, height
    return "", width, height


def _folder_layout(gui: str, folder_name: str, width: int, height: int) -> dict | None:
    """Ground a category's tab, icon template and tech-grid pitch in the GUI files."""
    cache_key = (id(gui), folder_name, width, height)
    if cache_key in _FOLDER_LAYOUT_CACHE:
        return _FOLDER_LAYOUT_CACHE[cache_key]
    view = _named_container(gui, "countrytechtreeview")
    folder = _named_container(gui, folder_name)
    template = _named_container(gui, f"techtree_{folder_name}_item")
    if not view or not folder or not template:
        _FOLDER_LAYOUT_CACHE[cache_key] = None
        return None
    view_pos, folder_pos, item_pos = _position(view), _position(folder), _position(template)
    icon = _named_node_position(template, "Icon", "iconType", 0, 0, absolute=False)
    tab = _named_node_position(gui, f"{folder_name}_tab", "buttonType", width, height)
    if not view_pos or not folder_pos or not item_pos or not icon or not tab:
        _FOLDER_LAYOUT_CACHE[cache_key] = None
        return None
    slot_sizes = set(re.findall(
        r"(?is)\bslotsize\s*=\s*\{\s*width\s*=\s*(\d+)\s+height\s*=\s*(\d+)", folder
    ))
    if len(slot_sizes) != 1:
        _FOLDER_LAYOUT_CACHE[cache_key] = None
        return None
    grid_width, grid_height = (int(value) for value in next(iter(slot_sizes)))
    margin = re.search(r"(?is)\bmargin\s*=\s*\{([^}]*)\}", folder)
    margins = margin.group(1) if margin else ""
    left_match = re.search(r"\bleft\s*=\s*(-?\d+)", margins)
    top_match = re.search(r"\btop\s*=\s*(-?\d+)", margins)
    layout = {
        "tab": tab,
        "view_pos": view_pos,
        "folder_pos": folder_pos,
        "item_pos": item_pos,
        "icon": icon,
        "margin_left": int(left_match.group(1)) if left_match else 0,
        "margin_top": int(top_match.group(1)) if top_match else 0,
        "grid_width": grid_width,
        "grid_height": grid_height,
    }
    _FOLDER_LAYOUT_CACHE[cache_key] = layout
    return layout


def _tech_pixel_position(item: dict, layout: dict | None = None,
                         width: int | None = None, height: int | None = None) -> tuple[int | None, int | None]:
    from actions.hoi4_live_state import _game_data_dir

    docs = _game_data_dir()
    width, height, scale = width or 1920, height or 1200, 1.0
    try:
        text = (docs / "settings.txt").read_text(encoding="utf-8-sig", errors="replace") if docs else ""
        size = re.search(r"(?m)^\s*size\s*=\s*\{\s*x\s*=\s*(\d+)\s*y\s*=\s*(\d+)", text)
        gui_scale = re.search(r"(?m)^\s*gui_scale\s*=\s*([\d.]+)", text)
        if size:
            width, height = int(size.group(1)), int(size.group(2))
        if gui_scale:
            scale = float(gui_scale.group(1))
    except OSError:
        pass
    if layout is None:
        gui, gui_width, gui_height = _technology_gui()
        layout = _folder_layout(gui, item.get("folder", ""), gui_width, gui_height) if gui else None
    if not layout:
        return None, None
    # Every offset and grid pitch comes from the active country technology GUI.
    x = (layout["view_pos"][0] + layout["folder_pos"][0] + layout["margin_left"]
         + item["x"] * layout["grid_width"] + layout["item_pos"][0] + layout["icon"][0]) * scale
    y = (layout["view_pos"][1] + layout["folder_pos"][1] + layout["margin_top"]
         + item["y"] * layout["grid_height"] + layout["item_pos"][1] + layout["icon"][1]) * scale
    if not (0 <= x < width and 0 <= y < height):
        return None, None
    return int(x), int(y)


def _named_container(gui: str, name: str) -> str | None:
    for node in _technology_gui_nodes(gui):
        if node["kind"] == "containerWindowType" and node["name"] == name:
            return node["block"]
    return None


def _named_node_position(gui: str, name: str, kind: str, width: int, height: int,
                         absolute: bool = True) -> tuple[float, float, float, float] | tuple[float, float] | None:
    from actions.hoi4_live_state import _scalar

    nodes = _technology_gui_nodes(gui)
    target = next((node for node in nodes if node["kind"] == kind and node["name"] == name), None)
    if not target or not target["pos"]:
        return None
    if not absolute:
        return target["pos"]
    parents = [node for node in nodes if node["kind"] in {"containerWindowType", "windowType"}
               and node["start"] < target["start"] and node["end"] > target["end"]]
    parents.sort(key=lambda node: node["start"])
    x, y = 0.0, 0.0
    for parent in parents:
        if not parent["pos"]:
            continue
        px, py = parent["pos"]
        x = width + x + px if "RIGHT" in parent["orient"] else x + px
        y = height + y + py if "LOWER" in parent["orient"] else y + py
    bx, by = target["pos"]
    orient = target["orient"]
    x = width + x + bx if "RIGHT" in orient else x + bx
    y = height + y + by if "LOWER" in orient else y + by
    x *= _gui_scale()
    y *= _gui_scale()
    size = re.search(r"(?i)\bsize\s*=\s*\{\s*(?:width|x)\s*=\s*(-?\d+)[^}]*?(?:height|y)\s*=\s*(-?\d+)", target["block"])
    if size:
        return x, y, float(size.group(1)) * _gui_scale(), float(size.group(2)) * _gui_scale()
    # Technology-tree tab textures are 182x61 with two horizontal frames.
    sprite_size = (91.0, 61.0)
    sprite_name = _scalar(target["block"], "quadTextureSprite")
    if name == "research":
        sprite_size = (123.0, 36.0)
    elif sprite_name:
        dimensions = _sprite_frame_size(sprite_name)
        if dimensions:
            sprite_size = dimensions
    return x, y, sprite_size[0] * _gui_scale(), sprite_size[1] * _gui_scale()


def _technology_gui_nodes(gui: str) -> list[dict]:
    """Parse GUI node blocks once; both catalog validation and task building share the index."""
    cache_key = id(gui)
    cached = _GUI_NODES_CACHE.get(cache_key)
    if cached is not None:
        return cached
    from actions.hoi4_live_state import _GUI_NODE, _matching_brace, _scalar

    nodes = []
    for match in _GUI_NODE.finditer(gui):
        open_at = gui.find("{", match.start(), match.end())
        close_at = _matching_brace(gui, open_at)
        if close_at is None:
            continue
        block = gui[open_at:close_at + 1]
        orient = _scalar(block, "Orientation") or _scalar(block, "orientation") or "UPPER_LEFT"
        nodes.append({
            "kind": match.group(1), "start": match.start(), "end": close_at,
            "name": _scalar(block, "name"), "block": block,
            "pos": _position(block), "orient": orient.upper(),
        })
    _GUI_NODES_CACHE[cache_key] = nodes
    return nodes


def _named_container_position(block: str) -> tuple[int, int] | None:
    return _position(block)


def _position(block: str) -> tuple[int, int] | None:
    match = re.search(r"(?i)\bposition\s*=\s*\{\s*x\s*=\s*(-?\d+(?:\.\d+)?)\s*y\s*=\s*(-?\d+(?:\.\d+)?)", block)
    if not match:
        return None
    return int(float(match.group(1))), int(float(match.group(2)))


def _gui_scale() -> float:
    from actions.hoi4_live_state import _game_data_dir

    try:
        text = (_game_data_dir() / "settings.txt").read_text(encoding="utf-8-sig", errors="replace")
        match = re.search(r"(?m)^\s*gui_scale\s*=\s*([\d.]+)", text)
        return float(match.group(1)) if match else 1.0
    except Exception:
        return 1.0


def _sprite_frame_size(sprite_name: str) -> tuple[float, float] | None:
    if sprite_name in _SPRITE_SIZE_CACHE:
        return _SPRITE_SIZE_CACHE[sprite_name]
    from actions.hoi4_live_state import _matching_brace, _scalar

    mod_root, game_root = _roots()
    roots = [root for root in (mod_root, game_root) if root]
    for root in roots:
        interface = root / "interface"
        # These installed GUI files define the sprites used by the task's
        # research tab and confirmation button; avoid indexing the full gfx tree.
        for gfx_path in (interface / "countrytechtreeview.gfx", interface / "core.gfx"):
            if not gfx_path.is_file():
                continue
            try:
                gfx = gfx_path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for match in re.finditer(r"(?m)^\s*(?:spriteType|frameAnimatedSpriteType|textSpriteType)\s*=\s*\{", gfx):
                open_at = gfx.find("{", match.start(), match.end())
                close_at = _matching_brace(gfx, open_at)
                if close_at is None:
                    continue
                block = gfx[open_at:close_at + 1]
                if _scalar(block, "name") != sprite_name:
                    continue
                texture = _scalar(block, "textureFile") or _scalar(block, "texturefile")
                if not texture:
                    continue
                image_path = root / texture.replace("/", "\\")
                try:
                    raw = image_path.read_bytes()[:32]
                    if raw[:4] != b"DDS ":
                        continue
                    h, w = struct.unpack_from("<II", raw, 12)
                except (OSError, struct.error):
                    continue
                frames = _int_scalar(block, "noOfFrames") or 1
                if frames > 1 and w % frames == 0:
                    w //= frames
                elif frames > 1 and h % frames == 0:
                    h //= frames
                _SPRITE_SIZE_CACHE[sprite_name] = (float(w), float(h))
                return _SPRITE_SIZE_CACHE[sprite_name]
    _SPRITE_SIZE_CACHE[sprite_name] = None
    return None


def _norm(value: float, maximum: int) -> int:
    return max(0, min(1000, round(value / maximum * 1000)))
