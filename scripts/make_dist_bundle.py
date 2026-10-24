"""Сборка установочного бандла JARVIS с телеметрией для отправки другим людям.

Использование:
    python scripts/make_dist_bundle.py --version 0.1.2

На выходе:
    dist/Jarvis-Install-Windows-v0.1.2-Telemetry.zip
"""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

_EXCLUDE_DIRS = {
    "__pycache__", ".venv", ".git", "dist", "logs", "cache",
    "models", "memory", "config", "skills", "mcp_servers",
    "android", "dashboard", "docs", "scripts/bootstrap",
    "scripts/__pycache__", "actions/__pycache__", "core/__pycache__",
    "jarvis/__pycache__", "memory/__pycache__", "plugins/__pycache__",
    "utils/__pycache__", "ui/__pycache__",
}

_EXCLUDE_FILES = {
    ".gitignore", ".python-version", "uv.lock", "pyproject.toml",
    "setup.py", "requirements.txt", "LICENSE",
    "README.md", "README_ru.md", "INSTALLER.md",
    "UPDATES.md", "UPDATES_LOCAL.md", "SETUP_LOCAL_UPDATES.txt",
    "INSTALL_LOCAL_TELEGRAM.md", "launch_hidden.vbs",
    "Запустить JARVIS.bat", "greeting.txt",
}


def _should_include(path: Path) -> bool:
    parts = set(path.parts)
    if parts & _EXCLUDE_DIRS:
        return False
    if path.name in _EXCLUDE_FILES:
        return False
    if path.suffix in (".pyc", ".pyo", ".bak"):
        return False
    return True


def build(version: str) -> Path:
    DIST.mkdir(parents=True, exist_ok=True)

    # 1. Собираем исходный ZIP приложения (как release.py, но проще)
    stage = DIST / f"_stage_{version}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    for item in ROOT.iterdir():
        if item.name in _EXCLUDE_DIRS or item.name in _EXCLUDE_FILES:
            continue
        if item.is_dir():
            shutil.copytree(item, stage / item.name, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.pyo", ".git", ".venv", "dist", "logs"
            ))
        else:
            shutil.copy2(item, stage / item.name)

    # 2. Добавляем телометрию (уже в core/telemetry.py, но проверим)
    telemetry_src = ROOT / "core" / "telemetry.py"
    if telemetry_src.exists():
        dst = stage / "core" / "telemetry.py"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(telemetry_src, dst)

    # 3. Добавляем установщик
    installer_src = ROOT / "scripts" / "bootstrap" / "setup-jarvis-local.ps1"
    if installer_src.exists():
        shutil.copy2(installer_src, stage / "setup-jarvis-local.ps1")

    # 4. Добавляем поллер (для разработчика, но не помешает)
    poller_src = ROOT / "scripts" / "error_bot_poller.py"
    if poller_src.exists():
        shutil.copy2(poller_src, stage / "error_bot_poller.py")

    # 5. Добавляем README
    readme = stage / "README-INSTALL.txt"
    readme.write_text(
        "JARVIS Installer\n"
        "================\n\n"
        "1. Распакуй этот ZIP в любую папку\n"
        "2. Запусти setup-jarvis-local.ps1 (правый клик -> Run with PowerShell)\n"
        "3. Подожди установки\n"
        "4. Запусти JARVIS через ярлык на Рабочем столе или run-jarvis.bat\n\n"
        "Телеметрия: ошибки отправляются разработчику автоматически.\n",
        encoding="utf-8",
    )

    # 6. Упаковываем
    zip_path = DIST / f"Jarvis-Install-Windows-v{version}-Telemetry.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in stage.rglob("*"):
            if item.is_file():
                zf.write(item, item.relative_to(stage).as_posix())

    # 7. Убираем временную папку
    shutil.rmtree(stage)

    size_mb = zip_path.stat().st_size / 1048576
    print(f"Собрано: {zip_path.name} ({size_mb:.1f} MB)")
    return zip_path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--version", required=True, help="Версия для имени файла")
    args = p.parse_args()
    build(args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
