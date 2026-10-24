#!/usr/bin/env python3
"""Маленький апдейтер JARVIS - безопасное обновление приложения без затрагивания user-data."""

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


def log(msg: str) -> None:
    print(f"[UPDATER] {msg}", flush=True)


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--app-dir", required=True)
    p.add_argument("--zip", required=True)
    p.add_argument("--restart-cmd", required=False, default="")
    args = p.parse_args()

    app_dir = Path(args.app_dir).resolve()
    zip_path = Path(args.zip).resolve()
    manifest_path = zip_path.with_suffix(".json")

    if not zip_path.exists():
        log(f"ZIP не найден: {zip_path}")
        return 1

    if not app_dir.exists():
        log(f"app-dir не найден: {app_dir}")
        return 1

    # Проверка SHA256 если есть манифест
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = m.get("sha256", "").lower()
            actual = sha256_file(zip_path).lower()
            if expected and actual != expected:
                log(f"SHA256 mismatch: {actual} != {expected}")
                return 1
            log(f"SHA256 проверка пройдена: {actual[:16]}...")
        except Exception as e:
            log(f"Не удалось проверить SHA256: {e}")
            return 1

    log(f"Распаковка {zip_path.name} в {app_dir}")
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmpd)

        # Находим корневую папку (Jarvis-vX.Y.Z)
        roots = [x for x in tmpd.iterdir() if x.is_dir()]
        src = tmpd / app_dir.name if (tmpd / app_dir.name).exists() else (roots[0] if roots else tmpd)

        # Исключаем пользовательские папки при копировании поверх
        exclude = {"config", "logs", "memory", "cache", "models", ".venv", "__pycache__"}

        for item in src.iterdir():
            dst = app_dir / item.name
            if item.name in exclude:
                log(f"Пропускаем user-data/preserved: {item.name}")
                continue
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)

    log("Обновление файлов приложения завершено (user-data не затронуты)")

    # Перезапуск
    if args.restart_cmd:
        log("Перезапускаем JARVIS...")
        try:
            # subprocess вместо os.system — ради таймаута: зависшая команда
            # раньше держала апдейтер вечно и обновление висло на последнем шаге.
            import subprocess as _sp

            _sp.run(args.restart_cmd, shell=True, timeout=30)
        except _sp.TimeoutExpired:
            log("Перезапуск не ответил за 30 секунд — продолжаю без него.")
        except Exception as e:
            log(f"Не удалось перезапустить: {e}")

    log("Готово")
    return 0


if __name__ == "__main__":
    sys.exit(main())
