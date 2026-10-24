"""Screenshot-grounded law changes for the HOI4 Millennium Dawn pilot.

The politics view is a scrollable list of law categories; each row shows the
icon of the currently active variant.  The pilot therefore never guesses a row
index: it resolves the current variant's ``GFX_idea_<id>`` sprite, finds that
exact icon on a live screenshot, opens the category, and only then looks for
the target variant icon.  A click is sent only after a confident match at both
stages.  If either icon cannot be confirmed, nothing is clicked and the task
is reported as failed.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from actions.hoi4_focus_gui import _click_at, _match_template, focus_icon_texture, grab_screen

_GUI_TOKEN = re.compile(
    r"(?m)^\s*(guiTypes|containerWindowType|windowType|buttonType|iconType|"
    r"gridBoxType|instantTextBoxType|editBoxType)\s*=\s*\{",
    re.I,
)
_NODE_CACHE: dict[str, tuple[int, str]] = {}
CURRENT_THRESHOLD = 0.62
TARGET_THRESHOLD = 0.66


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


def _gui_paths(name: str) -> list[Path]:
    from actions.hoi4_knowledge import discover_game_root, discover_mod_root

    paths: list[Path] = []
    for root in (discover_mod_root(), discover_game_root()):
        if not root:
            continue
        path = root / "interface" / f"{name}.gui"
        if path.is_file():
            paths.append(path)
    return paths


def _gui_text(name: str = "countrypoliticsview") -> str:
    for path in _gui_paths(name):
        try:
            stat = path.stat()
        except OSError:
            continue
        cached = _NODE_CACHE.get(str(path))
        if cached and cached[0] == stat.st_mtime_ns:
            return cached[1]
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        _NODE_CACHE[str(path)] = (stat.st_mtime_ns, text)
        return text
    return ""


def _nodes(text: str) -> list[dict]:
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
        size = re.search(
            r"(?i)\bsize\s*=\s*\{\s*(?:width\s*=\s*([\d.-]+%?)\s*height\s*=\s*([\d.-]+%?))",
            block,
        )

        def number(raw: str) -> float:
            return float(str(raw).rstrip("%"))

        slots = re.search(
            r"(?i)\bslotsize\s*=\s*\{\s*width\s*=\s*([\d.]+)\s*height\s*=\s*([\d.]+)", block
        )
        nodes.append({
            "kind": match.group(1).lower(),
            "name": _scalar(block, "name"),
            "start": match.start(),
            "end": end,
            "position": (float(pos.group(1)), float(pos.group(2))) if pos else None,
            "show_position": (float(show.group(1)), float(show.group(2))) if show else None,
            "size": (number(size.group(1)), number(size.group(2))) if size else None,
            "slot": (float(slots.group(1)), float(slots.group(2))) if slots else None,
            "orientation": (
                _scalar(block, "Orientation") or _scalar(block, "orientation") or "UPPER_LEFT"
            ).upper(),
        })
    return nodes


def _absolute(nodes: list[dict], name: str, kind: str | None = None) -> dict | None:
    """Resolve one named node to absolute game coordinates through its parents."""
    matches = [
        node for node in nodes
        if node["name"] == name and (kind is None or node["kind"] == kind)
    ]
    for target in matches:
        own = target["show_position"] or target["position"]
        if target["position"] is None:
            continue
        ancestors = [
            node for node in nodes
            if node["kind"] in {"containerwindowtype", "windowtype"}
            and node["start"] < target["start"] and node["end"] > target["end"]
        ]
        ancestors.sort(key=lambda node: node["start"])
        x = y = 0.0
        if ancestors:
            for index, parent in enumerate(ancestors):
                point = parent["show_position"] if index == 0 and parent["show_position"] else parent["position"]
                if not point:
                    continue
                x += point[0]
                y += point[1]
            base_x, base_y = x + target["position"][0], y + target["position"][1]
        elif own:
            # A top-level window's ``show_position`` is already absolute.
            base_x, base_y = own
        else:
            continue
        return {
            "x": base_x,
            "y": base_y,
            "size": target["size"],
            "slot": target["slot"],
            "node": target,
        }
    return None


def _settings() -> tuple[tuple[int, int], float, tuple[int, int]]:
    try:
        from actions.hoi4_live_state import _game_data_dir

        text = (_game_data_dir() / "settings.txt").read_text(encoding="utf-8-sig", errors="replace")
        size = re.search(r"(?m)^\s*size\s*=\s*\{\s*x\s*=\s*(\d+)\s*y\s*=\s*(\d+)", text)
        scale = re.search(r"(?m)^\s*gui_scale\s*=\s*([\d.]+)", text)
        game_size = (int(size.group(1)), int(size.group(2))) if size else (1920, 1200)
        gui_scale = float(scale.group(1)) if scale else 1.0
    except Exception:
        game_size, gui_scale = (1920, 1200), 1.0
    try:
        from actions.game_pilot import _pyautogui

        screen = _pyautogui().size()
        screen_size = (int(screen[0]), int(screen[1]))
    except Exception:
        screen_size = game_size
    return game_size, gui_scale, screen_size


def law_view_geometry() -> dict:
    """Absolute screen-pixel rectangles for the category list and variant list."""
    text = _gui_text("countrypoliticsview")
    nodes = _nodes(text)
    categories = _absolute(nodes, "idea_categories_grid", "gridboxtype")
    target_grid = _absolute(nodes, "box_list", "gridboxtype")
    window = next(
        (
            node for node in nodes
            if node["name"] == "political_ideas_window" and node["kind"] == "containerwindowtype"
        ),
        None,
    )
    game_size, gui_scale, screen_size = _settings()
    ratio_x = screen_size[0] / game_size[0] if game_size[0] else 1.0
    ratio_y = screen_size[1] / game_size[1] if game_size[1] else 1.0

    def to_screen(point: tuple[float, float]) -> tuple[int, int]:
        return (
            max(0, min(screen_size[0] - 1, int(point[0] * gui_scale * ratio_x))),
            max(0, min(screen_size[1] - 1, int(point[1] * gui_scale * ratio_y))),
        )

    cat_x, cat_y = (categories["x"], categories["y"]) if categories else (12.0, 516.0)
    cat_w, cat_h = categories.get("slot") if categories and categories.get("slot") else (480.0, 115.0)
    cat_h = max(80.0, min(220.0, cat_h))
    target_x, target_y = (target_grid["x"], target_grid["y"]) if target_grid else (552.0, 151.0)
    target_w = target_grid.get("slot", (462.0, 75.0))[0] if target_grid else 462.0
    window_y = (window["show_position"] or window["position"])[1] if window and (window["show_position"] or window["position"]) else 80.0
    window_h = 590.0
    if window and window["size"]:
        try:
            window_h = float(str(window["size"][1]).rstrip("%"))
        except ValueError:
            window_h = 590.0
    bottom = int((window_y + window_h) * gui_scale * ratio_y)

    cat_left, cat_top = to_screen((cat_x, cat_y))
    target_left, target_top = to_screen((target_x, target_y))
    # The category list fills the rest of the politics window height.
    categories_area = (
        cat_left,
        cat_top,
        max(120, int(cat_w * gui_scale * ratio_x)),
        max(115, screen_size[1] - cat_top - 8),
    )
    target_area = (
        target_left,
        target_top,
        max(120, int(target_w * gui_scale * ratio_x)),
        max(80, bottom - target_top - 8),
    )
    return {
        "categories_area": categories_area,
        "target_area": target_area,
        "category_slot": (int(cat_w * gui_scale * ratio_x), int(cat_h * gui_scale * ratio_y)),
        "target_slot": (int(target_w * gui_scale * ratio_x), int(75 * gui_scale * ratio_y)),
        "categories_scroll": (
            categories_area[0] + categories_area[2] // 2,
            categories_area[1] + categories_area[3] // 2,
        ),
        "target_scroll": (
            target_area[0] + target_area[2] // 2,
            target_area[1] + target_area[3] // 2,
        ),
        "game_size": game_size,
        "screen_size": screen_size,
        "gui_scale": gui_scale,
    }


def _load_icon(icon: str):
    from actions.hoi4_focus_gui import _texture_candidates

    for name in _texture_candidates(icon):
        rgba = focus_icon_texture(name)
        if rgba is not None:
            return rgba
    return None


def _idea_icon(variant_id: str):
    if not variant_id:
        return None
    from actions.hoi4_laws import law_groups

    group = next(
        (
            group for group in law_groups().values()
            if variant_id in group["variants"]
        ),
        None,
    )
    names = [f"GFX_idea_{variant_id}"]
    picture = (group or {}).get("variants", {}).get(variant_id, {}).get("picture")
    if picture:
        names.append(f"GFX_idea_{picture}")
    for name in names:
        rgba = _load_icon(name)
        if rgba is not None:
            return rgba
    return None


def _match_region(screen, rgba, area: tuple[int, int, int, int], factor: float, threshold: float):
    """Match *rgba* only inside *area* (screen pixels) and return real px."""
    import cv2
    import numpy as np

    x, y, width, height = area
    cx = max(0, int(x / factor))
    cy = max(0, int(y / factor))
    cw = min(screen.shape[1] - cx, max(1, int(width / factor)))
    ch = min(screen.shape[0] - cy, max(1, int(height / factor)))
    if cw < 12 or ch < 12:
        return None
    crop = screen[cy:cy + ch, cx:cx + cw]
    alpha = rgba[..., 3]
    ys, xs = np.nonzero(alpha > 16)
    if not len(xs):
        return None
    art = rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    gray = cv2.cvtColor(art[..., :3], cv2.COLOR_RGB2GRAY)
    opaque = gray[art[..., 3] > 64]
    fill = float(opaque.mean()) if opaque.size else 128.0
    template = np.full(gray.shape, fill, dtype=np.uint8)
    template[art[..., 3] > 16] = gray[art[..., 3] > 16]
    screen_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    best = None
    best_score = 0.0
    height_px, width_px = template.shape[:2]
    for scale in (0.55, 0.7, 0.85, 1.0, 1.15, 1.3):
        tw = max(10, int(width_px * scale / factor))
        th = max(10, int(height_px * scale / factor))
        if tw >= screen_gray.shape[1] or th >= screen_gray.shape[0]:
            continue
        resized = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(screen_gray, resized, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        if score > best_score:
            best_score = float(score)
            best = (loc[0], loc[1], tw, th)
    if best is None or best_score < threshold:
        return None
    bx, by, tw, th = best
    return (
        int((cx + bx + tw / 2) * factor),
        int((cy + by + th / 2) * factor),
        best_score,
    )


def _scroll_at(point: tuple[int, int], clicks: int) -> None:
    pag = _pag()
    try:
        pag.moveTo(point[0], point[1], duration=0.1)
    except Exception:
        pass
    pag.scroll(clicks)
    time.sleep(0.35)


def _pag():
    from actions.game_pilot import _pyautogui

    return _pyautogui()


def _find_icon_in_list(rgba, area, scroll_point, threshold: float):
    """Find an icon in a scrollable list, trying both scroll directions."""
    screen, factor = grab_screen()
    found = _match_region(screen, rgba, area, factor, threshold)
    if found:
        return found
    # Down first: newly opened lists are usually at the top.
    for _ in range(4):
        _scroll_at(scroll_point, -5)
        screen, factor = grab_screen()
        found = _match_region(screen, rgba, area, factor, threshold)
        if found:
            return found
    for _ in range(8):
        _scroll_at(scroll_point, 5)
        screen, factor = grab_screen()
        found = _match_region(screen, rgba, area, factor, threshold)
        if found:
            return found
    return None


def click_law(group_id: str, current_id: str, target_id: str) -> str:
    """Open the law category and click the target variant, or click nothing."""
    if not current_id or not target_id or current_id == target_id:
        return "failed law_click: invalid current/target law ids"
    try:
        from actions.game_pilot import _foreground_title, _is_hoi4_title

        if not _is_hoi4_title(_foreground_title()):
            return "failed law_click: HOI4 is not the foreground window; no input sent"
    except Exception:
        pass
    current_icon = _idea_icon(current_id)
    if current_icon is None:
        return f"failed law_click: icon GFX_idea_{current_id} not found in installed sprites"
    target_icon = _idea_icon(target_id)
    if target_icon is None:
        return f"failed law_click: icon GFX_idea_{target_id} not found in installed sprites"

    geometry = law_view_geometry()
    categories_area = geometry["categories_area"]
    target_area = geometry["target_area"]

    found = _find_icon_in_list(
        current_icon, categories_area, geometry["categories_scroll"], CURRENT_THRESHOLD
    )
    if not found:
        # The politics view may be closed; open it with its verified hotkey and
        # retry.  The current-law icon is the anchor, never a guessed row.
        pag = _pag()
        try:
            pag.press("q")
        except Exception as exc:
            return f"failed law_click: could not open politics view: {exc}"
        time.sleep(1.0)
        found = _find_icon_in_list(
            current_icon, categories_area, geometry["categories_scroll"], CURRENT_THRESHOLD
        )
    if not found:
        return (
            f"failed law_click: current {group_id} law icon {current_id} not found in the "
            "politics list; no click sent"
        )
    current_x, current_y, current_score = found
    _click_at(current_x, current_y)
    time.sleep(0.9)

    found = _find_icon_in_list(
        target_icon, target_area, geometry["target_scroll"], TARGET_THRESHOLD
    )
    if not found:
        return (
            f"failed law_click: category for {current_id} opened, but target icon "
            f"{target_id} was not confirmed; no click sent"
        )
    target_x, target_y, target_score = found
    _click_at(target_x, target_y)
    return (
        f"clicked law {group_id}: {current_id} -> {target_id} "
        f"(current icon {current_score:.2f}, target icon {target_score:.2f})"
    )
