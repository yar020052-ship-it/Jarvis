"""Screenshot-grounded national-focus clicking for the HOI4 pilot.

The in-game focus tree camera position and UI zoom are not stored in the save,
so a computed click can land on the wrong focus.  This module therefore never
guesses: it resolves the focus icon sprite from the installed GUI files, looks
for that exact icon on a live screenshot with OpenCV, and clicks only the
confirmed match.  When the target is off-screen it uses the focus view's own
search field (typed through the clipboard so Russian names work) and matches
again before clicking.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

_SPRITE_INDEX: dict[str, dict] | None = None
_TEXTURE_CACHE: dict[tuple[str, int], object] = {}
_LOCAL_TEXTURES: dict[str, dict] = {}


def _pag():
    from actions.game_pilot import _pyautogui

    return _pyautogui()


def _roots() -> list[tuple[str, Path]]:
    from actions.hoi4_knowledge import discover_game_root, discover_mod_root

    mod_root, game_root = discover_mod_root(), discover_game_root()
    roots: list[tuple[str, Path]] = []
    # Millennium Dawn overrides first, then base-game fallbacks.
    if mod_root:
        roots.append(("mod", mod_root))
    if game_root:
        roots.append(("base", game_root))
    return roots


def _scalar(text: str, key: str) -> str | None:
    match = re.search(
        rf"(?im)^\s*{re.escape(key)}\s*=\s*(?:\"([^\"]*)\"|([^\s#}}]+))", text
    )
    if not match:
        return None
    return (match.group(1) if match.group(1) is not None else match.group(2)).strip()


def sprite_index() -> dict[str, dict]:
    """Index focus/idea sprite names from installed ``.gfx`` files."""
    global _SPRITE_INDEX
    if _SPRITE_INDEX is not None:
        return _SPRITE_INDEX
    from actions.hoi4_live_state import _matching_brace

    index: dict[str, dict] = {}
    for _label, root in reversed(_roots()):
        folder = root / "interface"
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.gfx")):
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for match in re.finditer(r"(?im)^\s*(?:spriteType|frameAnimatedSpriteType|textSpriteType)\s*=\s*\{", text):
                open_at = text.find("{", match.start(), match.end())
                close_at = _matching_brace(text, open_at)
                if close_at is None:
                    continue
                block = text[open_at:close_at + 1]
                name = _scalar(block, "name")
                texture = _scalar(block, "textureFile")
                if not name or not texture:
                    continue
                index.setdefault(name, {"texture": texture, "block": block})
    _SPRITE_INDEX = index
    return index


def _texture_candidates(icon: str) -> list[str]:
    icon = (icon or "").strip()
    if not icon:
        return []
    names = [icon]
    if not icon.lower().startswith("gfx_"):
        names.extend([f"GFX_focus_{icon}", f"GFX_{icon}"])
    return names


def focus_icon_texture(icon: str):
    """Return the focus icon as an RGBA numpy array, or ``None``."""
    import numpy as np
    from PIL import Image

    index = sprite_index()
    entry = None
    for name in _texture_candidates(icon):
        entry = index.get(name)
        if entry:
            break
    if not entry:
        return None
    relative = entry["texture"].replace("\\", "/")
    for _label, root in _roots():
        path = root / relative
        try:
            stat = path.stat()
        except OSError:
            continue
        cache_key = (str(path), stat.st_mtime_ns)
        cached = _TEXTURE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        try:
            image = Image.open(path).convert("RGBA")
        except Exception:
            continue
        array = np.array(image)
        _TEXTURE_CACHE[cache_key] = array
        return array
    return None


def grab_screen(max_width: int = 1600):
    """Capture the primary screen as BGR plus the scale factor back to pixels."""
    import cv2
    import numpy as np

    pag = _pag()
    image = pag.screenshot()
    full_w, full_h = image.size
    width = min(full_w, max_width)
    if width < full_w:
        image = image.resize((width, int(full_h * width / full_w)))
    array = np.array(image.convert("RGB"))
    bgr = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
    return bgr, full_w / width


def _match_template(screen, rgba, factor: float, scales=(0.5, 0.65, 0.8, 1.0, 1.2, 1.4)):
    """Locate *rgba* in *screen*; return (pixel_x, pixel_y, score) or None."""
    import cv2
    import numpy as np

    alpha = rgba[..., 3]
    ys, xs = np.nonzero(alpha > 16)
    if not len(xs):
        return None
    crop = rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    gray = cv2.cvtColor(crop[..., :3], cv2.COLOR_RGB2GRAY)
    opaque = gray[crop[..., 3] > 64]
    fill = float(opaque.mean()) if opaque.size else 128.0
    # Neutral fill for transparent pixels keeps CCOEFF focused on the artwork.
    template = np.full(gray.shape, fill, dtype=np.uint8)
    template[crop[..., 3] > 16] = gray[crop[..., 3] > 16]
    screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    best_score = 0.0
    best = None
    height, width = template.shape[:2]
    for scale in scales:
        tw = max(12, int(width * scale / factor))
        th = max(12, int(height * scale / factor))
        if tw >= screen_gray.shape[1] or th >= screen_gray.shape[0]:
            continue
        resized = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(screen_gray, resized, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        if score > best_score:
            best_score = float(score)
            best = (loc[0], loc[1], tw, th)
    if best is None or best_score < 0.55:
        return None
    x, y, tw, th = best
    return (
        int((x + tw / 2) * factor),
        int((y + th / 2) * factor),
        best_score,
    )


def _click_at(x: int, y: int) -> None:
    _pag().click(x=x, y=y, button="left")


def _search_for_focus(focus_id: str, rgba) -> tuple[int, int, float] | None:
    """Type the localised focus name into the focus-view search field."""
    import pyperclip

    from actions.hoi4_focuses import focus_name, focus_view_geometry

    name = focus_name(focus_id)
    if not name or name == focus_id:
        return None
    pag = _pag()
    try:
        screen_w, screen_h = pag.size()
    except Exception:
        screen_w, screen_h = (1920, 1200)
    geometry = focus_view_geometry((screen_w, screen_h))
    sx, sy = geometry["search_click"]
    old_clipboard = ""
    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        pass
    try:
        _click_at(sx, sy)
        time.sleep(0.2)
        pag.hotkey("ctrl", "a")
        pag.press("backspace")
        pyperclip.copy(name)
        pag.hotkey("ctrl", "v")
        time.sleep(0.9)
    finally:
        try:
            pyperclip.copy(old_clipboard)
        except Exception:
            pass
    screen, factor = grab_screen()
    return _match_template(screen, rgba, factor)


def click_focus(focus_id: str) -> str:
    """Confirm and click one national focus. Never clicks without a match."""
    from actions.hoi4_focuses import _tree_definitions, active_tree
    from actions.hoi4_live_state import live_state

    state = live_state(force=True)
    tree = active_tree(state)
    if not tree:
        return "failed focus_click: active focus tree unknown"
    definition = _tree_definitions(tree).get(focus_id)
    if not definition:
        return f"failed focus_click: {focus_id} is not in the active tree"
    rgba = focus_icon_texture(definition.get("icon") or "")
    if rgba is None:
        return f"failed focus_click: icon sprite for {focus_id} not found"
    screen, factor = grab_screen()
    found = _match_template(screen, rgba, factor)
    if found:
        x, y, score = found
        _click_at(x, y)
        return f"clicked focus {focus_id} at {x},{y} (icon match {score:.2f})"
    found = _search_for_focus(focus_id, rgba)
    if found:
        x, y, score = found
        _click_at(x, y)
        return f"clicked focus {focus_id} at {x},{y} after search (icon match {score:.2f})"
    return f"failed focus_click: {focus_id} icon not found on screen; no click sent"
