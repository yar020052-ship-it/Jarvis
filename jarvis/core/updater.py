import json
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


def check_for_update(manifest_url: str, log: Callable[[str], None] = lambda x: None) -> Optional[UpdateInfo]:
    try:
        with _urlopen(manifest_url, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return UpdateInfo(data)
    except Exception as e:
        log(f"Update check failed: {e}")
        return None


def _ver_tuple(v: str):
    try:
        parts = v.split(".")
        out = []
        for p in parts:
            # выкидываем префиксы
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
        with _urlopen(url, timeout=60) as r:
            data = r.read()
        dest_zip.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_zip, "wb") as f:
            f.write(data)
        # save manifest for SHA
        dest_zip.with_suffix(".json").write_text(json.dumps(pinfo, ensure_ascii=False), encoding="utf-8")
        log(f"Downloaded: {dest_zip.name}")
        return True
    except Exception as e:
        log(f"Download failed: {e}")
        return False


def _detect_platform() -> str:
    import platform

    sysname = platform.system().lower()  # windows/linux/darwin
    mach = platform.machine().lower()  # x86_64/amd64/arm64
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
