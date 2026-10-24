"""Read the local record of actual JARVIS source-code changes."""

from pathlib import Path

try:
    from core.paths import MEMORY_DIR

    _CHANGELOG = MEMORY_DIR / "jarvis_changelog.md"
    # Миграция
    _old = Path(__file__).resolve().parent.parent / "memory" / "jarvis_changelog.md"
    if not _CHANGELOG.exists() and _old.exists():
        try:
            import shutil

            _CHANGELOG.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_old, _CHANGELOG)
        except Exception:
            pass
except Exception:
    _CHANGELOG = Path(__file__).resolve().parent.parent / "memory" / "jarvis_changelog.md"


def jarvis_changelog(parameters: dict | None = None, **_kwargs) -> str:
    """Return recorded changes; do not imply that JARVIS updates itself."""
    try:
        text = _CHANGELOG.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return f"Не удалось прочитать журнал изменений JARVIS: {exc}"
    if not text:
        return "В локальном журнале пока нет записей об изменениях кода JARVIS."
    return text


TOOL = {
    "name": "jarvis_changelog",
    "description": (
        "Reads the local JARVIS code-change log. Use whenever the user asks what "
        "changed in your code, what was modified in this installation, or asks "
        "for your recent code updates. This is the source of truth; do not claim "
        "you update yourself continuously or invent changes."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    "handler": jarvis_changelog,
}
