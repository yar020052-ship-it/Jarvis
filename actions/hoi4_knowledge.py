"""Local, read-only index of Hearts of Iron IV and Millennium Dawn scripts.

The mod describes available rules and content; it is not a live campaign API.
This module builds a compact searchable index once and refreshes it when files
change, so strategic prompts can use real installed content without shipping
thousands of script lines to a model on every decision.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path


_CACHE_VERSION = 2
_CACHE_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "JARVIS" / "hoi4_millennium_dawn_index.json"
_ROOT_HINTS = (
    Path.home() / "Documents" / "Paradox Interactive" / "Hearts of Iron IV" / "mod",
    Path.home() / "OneDrive" / "Documents" / "Paradox Interactive" / "Hearts of Iron IV" / "mod",
)
_FOCUS = re.compile(r"(?m)^\s*([A-Za-z0-9_.-]+)\s*=\s*\{[^{}]{0,500}?\bfocus\s*=\s*\{", re.S)
_TOP_BLOCK = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=\s*\{")
_KEYWORDS = re.compile(
    r"econom|industry|civilian|military|infrastructure|research|technology|tech|"
    r"focus|decision|politic|government|reform|stability|war support|trade|debt|"
    r"budget|tax|inflation|energy|oil|nuclear|army|air|navy|equipment|supply|"
    r"mobil|conscription|faction|diplomac|alliance|war|peace|annex|puppet|"
    r"эконом|промышлен|исслед|технолог|решени|полит|правительств|реформ|"
    r"стабил|бюджет|налог|долг|энерг|нефт|ядер|арм|авиац|флот|снабжен|"
    r"мобилиз|призыв|дипломат|альянс|войн|мир|аннекс|оккуп",
    re.I,
)
_ALLOWED_DIRS = (
    "national_focus", "decisions", "technologies", "ideas", "game_rules",
    "ai_strategy", "ai_strategy_plans", "ai_focuses", "ai_equipment",
    "ai_templates", "scripted_triggers", "scripted_effects", "defines",
    "buildings", "resources", "units", "country_tags", "bookmarks",
)
_MEMORY_INDEX = None
_LAST_FINGERPRINT_AT = 0.0
_FINGERPRINT_INTERVAL = 60.0


def _hoi4_docs() -> Path | None:
    for root in _ROOT_HINTS:
        if root.is_dir():
            return root.parent
    return None


def discover_mod_root() -> Path | None:
    """Resolve active Millennium Dawn mod descriptor into its installed path."""
    docs = _hoi4_docs()
    if not docs:
        return None
    active_file = docs / "dlc_load.json"
    active = []
    try:
        active = json.loads(active_file.read_text(encoding="utf-8-sig")).get("enabled_mods", [])
    except Exception:
        pass
    candidates = []
    for entry in active:
        descriptor = (docs / entry).resolve()
        if descriptor.is_file() and "millennium dawn" in descriptor.name.lower():
            candidates.append(descriptor)
    for descriptor in candidates:
        try:
            body = descriptor.read_text(encoding="utf-8-sig", errors="replace")
            match = re.search(r'(?m)^\s*path\s*=\s*"([^"]+)"', body)
            if match:
                path = (docs / match.group(1)).resolve()
                if path.is_dir():
                    return path
            for mod_root in _ROOT_HINTS:
                possible = mod_root / descriptor.stem
                if possible.is_dir():
                    return possible
        except Exception:
            continue
    # The launcher may not have written dlc_load.json until a playset is started.
    for mod_root in _ROOT_HINTS:
        for descriptor in mod_root.glob("*.mod"):
            if "millennium dawn" not in descriptor.name.lower():
                continue
            try:
                body = descriptor.read_text(encoding="utf-8-sig", errors="replace")
                match = re.search(r'(?m)^\s*path\s*=\s*"([^"]+)"', body)
                path = (docs / match.group(1)).resolve() if match else mod_root / descriptor.stem
                if path.is_dir():
                    return path
            except Exception:
                pass
    return None


def discover_game_root() -> Path | None:
    try:
        from actions.hoi4_live_state import game_install_dir

        return game_install_dir()
    except Exception:
        return None


def _mask_comments(text: str) -> str:
    # Keep newlines and character offsets stable while excluding # comments.
    return re.sub(r"#[^\r\n]*", lambda m: " " * len(m.group(0)), text)


def _blocks(text: str):
    """Yield top-level and direct child Clausewitz blocks in one linear scan."""
    clean = _mask_comments(text)
    stack = []
    line_start = 0
    i = 0
    quote = False
    escaped = False
    pending = None
    while i < len(clean):
        if i == line_start and len(stack) <= 1:
            match = _TOP_BLOCK.match(clean, line_start)
            if match:
                open_pos = clean.find("{", line_start, match.end())
                if open_pos >= 0:
                    pending = (match.group(1), line_start, open_pos)
        ch = clean[i]
        if ch == '"' and not escaped:
            quote = not quote
        if not quote:
            if ch == "{" and pending and pending[2] == i:
                key, start, _ = pending
                stack.append((key, start))
                pending = None
            elif ch == "}":
                if stack:
                    key, start = stack.pop()
                    if len(stack) <= 1:
                        yield key, text[start:i + 1], start
        escaped = ch == "\\" and not escaped
        if ch == "\n":
            line_start = i + 1
        i += 1


def _extract_entries(path: Path, root: Path, localisation: dict[str, str]) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    relative = path.relative_to(root).as_posix()
    kind = path.parent.name
    out = []
    clean = _mask_comments(text)
    for match in _TOP_BLOCK.finditer(clean):
        key = match.group(1)
        block = text[match.start():match.start() + 2400]
        searchable = key + " " + clean[match.start():match.start() + 2400]
        if len(block) < 24 or (kind not in {"national_focus", "decisions", "technologies", "ai_strategy", "ai_strategy_plans", "ai_focuses", "ai_equipment", "ai_templates"}
                              and not _KEYWORDS.search(searchable)):
            continue
        # Content digest makes stale entries detectable; the full script remains on disk.
        excerpt = re.sub(r"\s+", " ", _mask_comments(block))[:720].strip()
        title = localisation.get(key, key)
        out.append({"id": key, "name": title, "kind": kind, "source": relative, "excerpt": excerpt})
    return out


def _localisation(root: Path) -> dict[str, str]:
    result = {}
    loc_root = root / "localisation"
    if not loc_root.is_dir():
        return result
    for path in loc_root.rglob("*_l_english.yml"):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = re.match(r"\s*([\w.-]+):\d*\s+\"(.*?)\"\s*$", line)
            if match:
                result.setdefault(match.group(1), match.group(2).replace("\\n", " "))
    return result


def build_index(root: Path, game_root: Path | None = None) -> dict:
    roots = [("mod", root)]
    if game_root and game_root.resolve() != root.resolve():
        roots.append(("base", game_root))
    localisation = {}
    for _label, source_root in reversed(roots):
        localisation.update(_localisation(source_root))
    entries = []
    files = []
    for label, source_root in roots:
        for directory in _ALLOWED_DIRS:
            folder = source_root / "common" / directory
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*.txt")):
                try:
                    stat = path.stat()
                    files.append([f"{label}/{path.relative_to(source_root).as_posix()}", stat.st_size, stat.st_mtime_ns])
                except OSError:
                    continue
                for item in _extract_entries(path, source_root, localisation):
                    item["source"] = f"{label}/{item['source']}"
                    entries.append(item)
    return {
        "version": _CACHE_VERSION,
        "root": str(root),
        "game_root": str(game_root) if game_root else "",
        "built_at": time.time(),
        "files": files,
        "entries": entries,
        "counts": {},
    }


def _source_fingerprint(root: Path, game_root: Path | None = None) -> list:
    result = []
    roots = [("mod", root)]
    if game_root and game_root.resolve() != root.resolve():
        roots.append(("base", game_root))
    for label, source_root in roots:
        for directory in _ALLOWED_DIRS:
            folder = source_root / "common" / directory
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*.txt")):
                try:
                    stat = path.stat()
                    result.append([f"{label}/{path.relative_to(source_root).as_posix()}", stat.st_size, stat.st_mtime_ns])
                except OSError:
                    continue
    return result


def get_index(force: bool = False) -> dict | None:
    global _MEMORY_INDEX, _LAST_FINGERPRINT_AT
    root = discover_mod_root()
    if not root:
        return None
    game_root = discover_game_root()
    if (not force and _MEMORY_INDEX
            and _MEMORY_INDEX.get("root") == str(root)
            and _MEMORY_INDEX.get("game_root", "") == (str(game_root) if game_root else "")
            and time.monotonic() - _LAST_FINGERPRINT_AT < _FINGERPRINT_INTERVAL):
        return _MEMORY_INDEX
    current = _source_fingerprint(root, game_root)
    if not force:
        try:
            cached = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            if (cached.get("version") == _CACHE_VERSION and cached.get("root") == str(root)
                    and cached.get("game_root", "") == (str(game_root) if game_root else "")
                    and cached.get("files") == current):
                _MEMORY_INDEX = cached
                _LAST_FINGERPRINT_AT = time.monotonic()
                return cached
        except Exception:
            pass
    index = build_index(root, game_root)
    by_kind = {}
    for item in index["entries"]:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    index["counts"] = by_kind
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    _MEMORY_INDEX = index
    _LAST_FINGERPRINT_AT = time.monotonic()
    return index


def search(query: str, limit: int = 14) -> list[dict]:
    index = get_index()
    if not index:
        return []
    aliases = {
        "экономика": "economy economic budget tax industry civilian factory construction debt",
        "экономику": "economy economic budget tax industry civilian factory construction debt",
        "промышленность": "industry factory civilian military construction production",
        "исследование": "research technology technologies electronics industry",
        "технологии": "research technology technologies electronics industry",
        "фокус": "national focus focus tree",
        "решение": "decision decisions political economy",
        "политика": "political government election ideology reform law",
        "армия": "army military equipment manpower supply doctrine",
        "война": "war military army air naval equipment supply",
        "дипломатия": "diplomacy faction alliance opinion influence",
        "энергетика": "energy oil electricity nuclear resources",
    }
    expanded = [query]
    for word, expansion in aliases.items():
        if word in query.lower():
            expanded.append(expansion)
    terms = list(dict.fromkeys(word.lower() for value in expanded for word in re.findall(r"[\w-]+", value) if len(word) > 2))
    if not terms:
        terms = ["ai", "econom", "research", "focus"]
    scored = []
    for item in index["entries"]:
        haystack = f"{item['id']} {item['name']} {item['kind']} {item['excerpt']}".lower()
        score = sum(haystack.count(term) * (3 if term in item["id"].lower() or term in item["name"].lower() else 1) for term in terms)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [item for _, item in scored[:max(1, min(limit, 30))]]


def prompt_context(query: str, limit: int = 12) -> str:
    index = get_index()
    if not index:
        return "Millennium Dawn local scripts could not be found; base decisions on the live game state."
    results = search(query, limit)
    lines = [
        "Local installed-mod knowledge index (read-only scripts, not live campaign state).",
        f"Indexed {len(index['files'])} script files; entries by category: {index.get('counts', {})}.",
        "Use these entries as available options and rules. Verify current eligibility in game before selecting.",
    ]
    for item in results:
        lines.append(f"- [{item['kind']}] {item['name']} ({item['id']}; {item['source']}): {item['excerpt']}")
    return "\n".join(lines)

