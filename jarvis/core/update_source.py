"""Альтернативный источник обновлений: локальный/сетевой файл (не GitHub).

Варианты:
- Файл latest.json на локальном сервере (http://192.168.1.100:8080/latest.json)
- Или просто проверять обновление по zip-файлу в общей папке/по ссылке на прямую загрузку
- Также можно хранить manifest в user-share (например, рядом с инсталлятором)

Сделаем гибкий: можно задать UPDATE_MANIFEST_URL через env или в config.
Если не задано — проверка отключается (или можно указать file://)
"""
import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Optional

try:
    from ..__version__ import __version__
except Exception:
    try:
        from __version__ import __version__  # type: ignore
    except Exception:
        __version__ = "0.1.0"

APP_VERSION = __version__
DEFAULT_MANIFEST = os.environ.get("JARVIS_UPDATE_MANIFEST") or os.environ.get("JARVIS_UPDATE_URL") or ""


def _urlopen(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Updater/1.0"})
    return urllib.request.urlopen(req, timeout=timeout)


class UpdateInfo:
    def __init__(self, data: dict):
        self.data = data
        self.version = str(data.get("version", ""))
        self.channel = str(data.get("channel", "stable"))
        self.min_supported = str(data.get("min_supported", "0.0.0"))
        self.changelog = str(data.get("changelog", ""))
        self.changelog_ru = str(data.get("changelog_ru", ""))


def _load_manifest(url: str) -> dict:
    if url.startswith("file://"):
        p = Path(url[7:])
        return json.loads(p.read_text(encoding="utf-8"))
    if url.startswith("http://") or url.startswith("https://"):
        with _urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    # локальный путь
    p = Path(url)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Manifest not found: {url}")


def check_for_update(manifest_url: str = DEFAULT_MANIFEST, log: Callable[[str], None] = lambda x: None) -> Optional[UpdateInfo]:
    if not manifest_url:
        log("No update manifest URL configured")
        return None
    try:
        data = _load_manifest(manifest_url)
        return UpdateInfo(data)
    except Exception as e:
        log(f"Update check failed: {e}")
        return None


def _ver_tuple(v: str):
    try:
        parts = v.split(".")
        out = []
        for p in parts:
            num = ""
            for ch in p:
                if ch.isdigit():
                    num += ch
                else:
                    break
            out.append(int(num) if num else 0)
        return tuple(out + [0] * (3 - len(out)))
    except Exception:
        return (0, 0, 0)


def is_newer(new_ver: str, cur_ver: str = APP_VERSION) -> bool:
    return _ver_tuple(new_ver) > _ver_tuple(cur_ver)


def download_update(info: UpdateInfo, platform_key: str, dest_zip: Path, log: Callable[[str], None] = lambda x: None) -> bool:
    plats = info.data.get("platforms", {})
    pinfo = plats.get(platform_key) or plats.get(platform_key.replace("-", "_"))
    if not pinfo:
        log(f"No update for platform: {platform_key}")
        return False
    url = pinfo.get("url")
    if not url:
        return False
    try:
        if url.startswith("file://"):
            src = Path(url[7:])
            dest_zip.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy2(src, dest_zip)
        elif url.startswith("http://") or url.startswith("https://"):
            with _urlopen(url, timeout=120) as r:
                data = r.read()
            dest_zip.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_zip, "wb") as f:
                f.write(data)
        else:
            src = Path(url)
            dest_zip.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy2(src, dest_zip)
        dest_zip.with_suffix(".json").write_text(json.dumps(pinfo, ensure_ascii=False), encoding="utf-8")
        log(f"Downloaded: {dest_zip.name}")
        return True
    except Exception as e:
        log(f"Download failed: {e}")
        return False


def _detect_platform():
    import platform

    sysname = platform.system().lower()
    mach = platform.machine().lower()
    if mach in ("amd64", "x86_64", "x64"):
        arch = "x64"
    elif mach in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = mach
    if sysname == "windows":
        return f"windows-{arch}"
    if sysname == "darwin":
        return f"darwin-{arch}"
    if sysname == "linux":
        return f"linux-{arch}"
    return f"{sysname}-{arch}"


__all__ = ["UpdateInfo", "check_for_update", "is_newer", "download_update", "_detect_platform", "APP_VERSION"]
