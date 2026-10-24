"""Снапшоты и откат: страховка перед любой правкой кода."""
from __future__ import annotations

import json
import shutil
import time

from . import BASE_DIR, STATE_DIR, is_editable

SNAP_DIR = STATE_DIR / "snapshots"
MAX_KEEP = 30


def snapshot(rel_paths: list[str], tag: str, reason: str = "") -> str:
    """Сохранить копии файлов. Возвращает id снапшота."""
    sid = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}-{tag}"
    dest = SNAP_DIR / sid
    dest.mkdir(parents=True, exist_ok=True)
    files = []
    for rel in rel_paths:
        if not is_editable(rel):
            raise ValueError(f"Вне разрешённой зоны: {rel}")
        src = BASE_DIR.joinpath(*rel.replace("\\", "/").split("/"))
        if not src.is_file():
            continue
        # Keep the project-relative path: basenames collide across directories.
        target = dest.joinpath(*rel.replace("\\", "/").split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        files.append({"rel": rel, "name": target.name})
    (dest / "meta.json").write_text(
        json.dumps({"id": sid, "tag": tag, "reason": reason,
                    "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "files": files}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    _gc()
    return sid


def rollback(sid: str) -> list[str]:
    """Вернуть файлы из снапшота. Возвращает список восстановленных."""
    dest = SNAP_DIR / sid
    meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    restored = []
    for f in meta.get("files", []):
        rel = str(f.get("rel", "")).replace("\\", "/")
        if not is_editable(rel):
            raise ValueError(f"Вне разрешённой зоны в снимке: {rel}")
        # Read older snapshots that stored the flattened basename separately.
        src = dest.joinpath(*rel.split("/"))
        if not src.is_file() and f.get("name"):
            src = dest / str(f["name"])
        target = (BASE_DIR / rel).resolve()
        if not target.is_relative_to(BASE_DIR.resolve()):
            raise ValueError(f"Путь снимка выходит за проект: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        restored.append(rel)
    return restored


def rollback_for_incident(incident_id: str) -> list[str]:
    """Откатить последний снимок ремонта для инцидента при аварии repair."""
    if not SNAP_DIR.exists():
        return []
    for d in sorted(SNAP_DIR.iterdir(), reverse=True):
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("tag") == "fix" and meta.get("reason") == f"repair {incident_id}":
            return rollback(str(meta.get("id") or d.name))
    return []


def list_snaps(limit: int = 20) -> list[dict]:
    out = []
    if SNAP_DIR.exists():
        for d in sorted(SNAP_DIR.iterdir(), reverse=True)[:limit]:
            try:
                out.append(json.loads((d / "meta.json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    return out


def _gc() -> None:
    if not SNAP_DIR.exists():
        return
    for d in sorted(SNAP_DIR.iterdir(), reverse=True)[MAX_KEEP:]:
        shutil.rmtree(d, ignore_errors=True)
