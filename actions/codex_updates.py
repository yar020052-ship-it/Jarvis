"""Check the public Codex changelog and remember the newest reviewed entry."""

from __future__ import annotations

import json
import re
from datetime import date
from html import unescape
from pathlib import Path

_CHANGELOG_URL = "https://learn.chatgpt.com/docs/changelog"
try:
    from core.paths import MEMORY_DIR

    _STATE_FILE = MEMORY_DIR / "codex_updates_state.json"
    _old = Path(__file__).resolve().parent.parent / "memory" / "codex_updates_state.json"
    if not _STATE_FILE.exists() and _old.exists():
        try:
            import shutil

            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_old, _STATE_FILE)
        except Exception:
            pass
except Exception:
    _STATE_FILE = Path(__file__).resolve().parent.parent / "memory" / "codex_updates_state.json"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _entries() -> list[dict[str, str]]:
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(
        _CHANGELOG_URL,
        headers={"User-Agent": "JARVIS-Codex-Update-Reader/1.0"},
        timeout=(8, 20),
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    lines = [unescape(line).strip() for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]

    results: list[dict[str, str]] = []
    index = 0
    while index < len(lines):
        if not _DATE.fullmatch(lines[index]):
            index += 1
            continue
        published = lines[index]
        end = index + 1
        while end < len(lines) and not _DATE.fullmatch(lines[end]):
            end += 1
        block = lines[index + 1 : end]
        index = end
        if not any("codex" in item.lower() for item in block):
            continue

        # Changelog blocks can include full explanatory text. Keep the heading
        # and a few concise details so the voice assistant can assess relevance.
        useful = [
            item for item in block
            if item.lower() != "codex"
            and not item.startswith(("Image:", "https://"))
        ]
        title = next(
            (item for item in useful if "codex" in item.lower() or "Introducing " in item),
            useful[0] if useful else "Обновление Codex",
        )
        details = [item for item in useful if item != title]
        results.append({
            "date": published,
            "title": title[:240],
            "details": " ".join(details[:3])[:900],
            "url": _CHANGELOG_URL,
        })

    results.sort(key=lambda item: item["date"], reverse=True)
    return results


def codex_updates(parameters: dict | None = None, **_kwargs) -> str:
    """Return new Codex changelog entries and record the latest checked date."""
    import requests

    try:
        entries = _entries()
        if not entries:
            return "В официальном журнале пока не удалось найти записи об обновлениях Codex."

        try:
            state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            last_seen = str(state.get("last_seen_date") or "")
        except (OSError, ValueError, AttributeError):
            last_seen = ""

        fresh = [entry for entry in entries if entry["date"] > last_seen] if last_seen else entries[:5]
        newest = entries[0]["date"]
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps(
                {"last_seen_date": max(newest, last_seen), "checked_on": date.today().isoformat()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        if not fresh:
            return f"Новых записей в журнале Codex после {last_seen} не найдено. Источник: {_CHANGELOG_URL}"
        heading = "Последние записи журнала Codex:" if not last_seen else "Новые записи журнала Codex:"
        lines = [heading]
        for entry in fresh[:5]:
            lines.append(f"- {entry['date']}: {entry['title']}. {entry['details']}")
        if len(fresh) > 5:
            lines.append(f"Показаны 5 из {len(fresh)} новых записей.")
        lines.append(f"Источник: {_CHANGELOG_URL}")
        lines.append(
            "Это журнал продукта Codex; записи сами по себе не меняют код JARVIS. "
            "Совместимые идеи нужно адаптировать к его архитектуре отдельно."
        )
        return "\n".join(lines)
    except requests.RequestException as exc:
        return f"Не удалось проверить журнал Codex из-за сетевой ошибки: {exc}"
    except Exception as exc:
        return f"Не удалось прочитать журнал обновлений Codex: {exc}"


TOOL = {
    "name": "codex_updates",
    "description": (
        "Checks the public OpenAI Codex product changelog, reports recent or newly "
        "published entries, and remembers the latest checked date. Use when the user "
        "asks what is new in Codex or asks JARVIS to review Codex updates for useful "
        "ideas. Explain in Russian which ideas can be adapted to JARVIS. This only "
        "reads the changelog; it never downloads or applies external code."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    "handler": codex_updates,
}
