#!/usr/bin/env python3
"""Скрипт подготовки релиза: сборка архивов + SHA256 + latest.json."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def zip_dir(src: Path, dst_zip: Path) -> None:
    dst_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(src):
            # исключаем тяжелые build мусор? оставим минимально
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".venv", "build", "dist", ".git", "logs", "android")]
            for f in files:
                if f.endswith((".pyc", ".pyo")):
                    continue
                full = Path(root) / f
                rel = full.relative_to(src)
                z.write(full, arcname=rel)


def write_version(root: Path, version: str) -> None:
    for rel in ("__version__.py", "jarvis/__version__.py"):
        path = root / rel
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        out = []
        changed = False
        for line in lines:
            if line.startswith("__version__"):
                out.append(f'__version__ = "{version}"')
                changed = True
            else:
                out.append(line)
        if changed:
            path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--version", required=True)
    p.add_argument("--dist", default="dist")
    p.add_argument("--repo", default="yar020052-ship-it/Jarvis", help="GitHub repo as owner/name")
    p.add_argument("--auto-install", action="store_true", help="Clients install this update automatically")
    p.add_argument("--changelog-ru", default="See GitHub release")
    args = p.parse_args()

    ver = args.version.strip().lstrip("v")
    repo = args.repo.strip().strip("/")
    dist = Path(args.dist)
    dist.mkdir(parents=True, exist_ok=True)

    root = Path(__file__).resolve().parent.parent
    write_version(root, ver)
    pkg_name = f"Jarvis-v{ver}"

    # Собираем в temp
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        build = tmpd / pkg_name
        build.mkdir()
        # копируем всё кроме исключений
        for item in root.iterdir():
            name = item.name
            if name in ("dist", "__pycache__", ".git", "logs", "venv", "android"):
                continue
            if item.is_dir():
                try:
                    shutil.copytree(item, build / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", ".git"), dirs_exist_ok=True)
                except Exception:
                    pass
            else:
                try:
                    shutil.copy2(item, build / name)
                except Exception:
                    pass

        # архивы под платформы (универсальный zip для релиза)
        zips = []
        for plat in ("windows-x64", "linux-x64", "darwin-x64", "darwin-arm64", "windows-arm64"):
            zpath = dist / f"{pkg_name}-{plat}.zip"
            zip_dir(build, zpath)
            zips.append((plat, zpath))

    # SHA256
    sha = dist / "SHA256SUMS.txt"
    lines = []
    for plat, zpath in zips:
        h = sha256_file(zpath)
        lines.append(f"{h}  {zpath.name}")
    sha.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # latest.json
    latest = {
        "version": f"{ver}",
        "channel": "stable",
        "min_supported": "0.1.0",
        "released_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "changelog": f"https://github.com/{repo}/releases/tag/v{ver}",
        "changelog_ru": args.changelog_ru,
        "auto_install": bool(args.auto_install),
        "platforms": {},
        "notes": "Safe update: user data is preserved",
    }
    for plat, zpath in zips:
        latest["platforms"][plat] = {
            "url": f"https://github.com/{repo}/releases/download/v{ver}/{zpath.name}",
            "sha256": sha256_file(zpath),
            "size_mb": round(zpath.stat().st_size / (1024 * 1024), 1),
        }
    (dist / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Done:", dist.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
