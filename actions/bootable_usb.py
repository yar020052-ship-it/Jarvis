"""Verified ISO downloads and guarded Ventoy boot-USB creation for Windows."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from core.confirm import request as request_confirmation

_ALLOWED_HOSTS = {
    "ubuntu.com", "releases.ubuntu.com", "cdimage.ubuntu.com",
    "fedoraproject.org", "download.fedoraproject.org", "getfedora.org",
    "debian.org", "cdimage.debian.org", "archlinux.org",
    "opensuse.org", "download.opensuse.org", "microsoft.com",
    "software-download.microsoft.com",
}
_VENTOY_PAGE = "https://www.ventoy.net/en/download.html"
_MAX_ISO_BYTES = 20 * 1024**3


def _is_official_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == domain or host.endswith("." + domain) for domain in _ALLOWED_HOSTS
    )


def _is_public_https(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in {"localhost"} or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host


def _host_allowed(
    url: str, extra_hosts: tuple[str, ...] = (), allow_unofficial: bool = False
) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return _is_public_https(url) and (
        _is_official_url(url) or any(
        host == domain or host.endswith("." + domain) for domain in extra_hosts
        ) or allow_unofficial
    )


def _request(
    url: str, timeout: int = 30, extra_hosts: tuple[str, ...] = (),
    require_trusted_redirect: bool = True, allow_unofficial: bool = False,
):
    if not _host_allowed(url, extra_hosts, allow_unofficial):
        raise ValueError("Нужна публичная HTTPS-ссылка. Локальные адреса и HTTP заблокированы.")
    request = urllib.request.Request(
        url, headers={"User-Agent": "JARVIS-BootMedia/1.0"}
    )
    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not _is_public_https(newurl):
                raise ValueError("Перенаправление на небезопасный или локальный адрес заблокировано.")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(SafeRedirect())
    response = opener.open(request, timeout=timeout)
    final = urllib.parse.urlparse(response.geturl()).hostname or ""
    allowed_final = any(final == d or final.endswith("." + d) for d in _ALLOWED_HOSTS)
    allowed_final = allowed_final or any(
        final == d or final.endswith("." + d) for d in extra_hosts
    )
    if urllib.parse.urlparse(response.geturl()).scheme != "https":
        response.close()
        raise ValueError("Сервер попытался продолжить загрузку без HTTPS.")
    if require_trusted_redirect and not allowed_final:
        if allow_unofficial and _is_public_https(response.geturl()):
            return response
        response.close()
        raise ValueError("Официальный сервер перенаправил загрузку на неизвестный домен.")
    return response


def _download(
    url: str, target: Path, max_bytes: int, timeout: int = 60,
    extra_hosts: tuple[str, ...] = (), allow_unofficial: bool = False,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    total = 0
    try:
        with _request(
            url, timeout=timeout, extra_hosts=extra_hosts,
            require_trusted_redirect=False, allow_unofficial=allow_unofficial,
        ) as response, temp.open("wb") as out:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ValueError("Файл превышает допустимый размер загрузки.")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Файл превышает допустимый размер загрузки.")
                out.write(chunk)
        if total == 0:
            raise RuntimeError("Сервер вернул пустой файл.")
        temp.replace(target)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_root() -> Path:
    return Path.home() / "Downloads" / "JARVIS-boot-media"


def _download_iso(params: dict) -> str:
    url = str(params.get("iso_url") or "").strip()
    sums_url = str(params.get("checksum_url") or "").strip()
    expected_direct = str(params.get("expected_sha256") or "").strip().lower()
    allow_unofficial = bool(params.get("allow_unofficial"))
    official_source = _is_official_url(url)
    if not official_source and not allow_unofficial:
        raise ValueError("Для стороннего сайта нужно явное указание allow_unofficial=true.")
    name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    requested = str(params.get("filename") or name).strip()
    requested = Path(requested).name
    if not url.lower().endswith(".iso") and not name.lower().endswith(".iso"):
        raise ValueError("Ссылка должна вести непосредственно на ISO-образ.")
    if not requested.lower().endswith(".iso") or not re.fullmatch(r"[\w .()+-]{1,180}\.iso", requested, re.I):
        raise ValueError("Некорректное имя ISO-файла.")
    expected = None
    if sums_url:
        if not _is_official_url(sums_url) and not allow_unofficial:
            raise ValueError("Для стороннего файла контрольной суммы нужно разрешить сторонний источник.")
        with _request(sums_url, allow_unofficial=allow_unofficial) as response:
            sums_text = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        for line in sums_text.splitlines():
            match = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
            if match and Path(match.group(2).strip()).name.lower() == requested.lower():
                expected = match.group(1).lower()
                break
    elif official_source and any(
        (urllib.parse.urlparse(url).hostname or "").lower() == domain
        or (urllib.parse.urlparse(url).hostname or "").lower().endswith("." + domain)
        for domain in ("microsoft.com",)
    ):
        if re.fullmatch(r"[0-9a-f]{64}", expected_direct):
            expected = expected_direct
    if not expected:
        source_kind = "публикованная SHA-256 сумма" if not official_source else "официальная SHA-256 сумма"
        raise ValueError(f"Нужна {source_kind}: из SHA256SUMS либо со страницы Microsoft для этого ISO.")

    target = _download_root() / requested
    target.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(target.parent).free
    if free < 2 * 1024**3:
        raise RuntimeError("На системном диске недостаточно места для загрузки ISO.")
    if not target.exists() or _sha256(target) != expected:
        _download(url, target, _MAX_ISO_BYTES, timeout=600, allow_unofficial=allow_unofficial)
    actual = _sha256(target)
    if actual != expected:
        target.unlink(missing_ok=True)
        raise RuntimeError("Контрольная сумма ISO не совпала; повреждённый файл удалён.")
    target.with_suffix(target.suffix + ".sha256").write_text(expected, encoding="ascii")
    source_info = {
        "source_url": url,
        "checksum_url": sums_url or "официальная страница Microsoft",
        "official_source": official_source,
    }
    target.with_suffix(target.suffix + ".source.json").write_text(
        json.dumps(source_info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trust_note = "официального источника" if official_source else "стороннего источника; хэш подтверждает целостность, но не подлинность издателя"
    return f"ISO загружен из {urllib.parse.urlparse(url).hostname} и совпал с опубликованной SHA-256 суммой ({trust_note}): {target}"


def _usb_disks() -> list[dict]:
    if os.name != "nt":
        raise RuntimeError("Подготовка USB-флешки поддерживается только в Windows.")
    script = (
        "Get-Disk | Where-Object { $_.BusType -eq 'USB' } | "
        "Select-Object Number,FriendlyName,UniqueId,Size,BusType,IsBoot,IsSystem,IsOffline | "
        "ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or "Не удалось прочитать список USB-дисков.").strip())
    raw = result.stdout.strip()
    if not raw:
        return []
    data = json.loads(raw)
    return data if isinstance(data, list) else [data]


def _disk_snapshot(number: int) -> dict:
    matches = [d for d in _usb_disks() if int(d.get("Number", -1)) == number]
    if len(matches) != 1:
        raise RuntimeError("USB-диск с таким номером не найден. Повторно проверьте список устройств.")
    disk = matches[0]
    if disk.get("IsBoot") or disk.get("IsSystem"):
        raise RuntimeError("Отказ: выбран системный или загрузочный диск компьютера.")
    return disk


def _display_disks() -> str:
    disks = [d for d in _usb_disks() if not d.get("IsBoot") and not d.get("IsSystem")]
    if not disks:
        return "USB-флешки не обнаружены. Подключите флешку, затем повторите проверку."
    lines = ["Найденные USB-диски (номер, модель, объём):"]
    for disk in disks:
        size_gb = int(disk.get("Size") or 0) / (1024**3)
        lines.append(f"№ {disk.get('Number')}: {disk.get('FriendlyName') or 'USB-накопитель'}, {size_gb:.1f} ГБ")
    lines.append("Перед созданием загрузочного носителя выбранная флешка будет полностью очищена.")
    return "\n".join(lines)


def _ventoy_package() -> Path:
    # The download page publishes both the current Windows archive and its
    # SHA-256 digest. Refuse to install if the upstream page format changes.
    with _request(_VENTOY_PAGE) as response:
        page = response.read(3 * 1024 * 1024).decode("utf-8", errors="replace")
    match = re.search(r"ventoy-(\d+\.\d+\.\d+)-windows\.zip", page, re.I)
    if not match:
        raise RuntimeError("Не удалось определить актуальную версию Ventoy на официальном сайте.")
    version = match.group(1)
    archive_name = f"ventoy-{version}-windows.zip"
    row_match = re.search(
        rf"(?is)<tr\b[^>]*>(?:(?!</tr>).)*?{re.escape(archive_name)}(?:(?!</tr>).)*?</tr>",
        page,
    )
    if not row_match:
        raise RuntimeError("На сайте Ventoy не найдена строка с контрольной суммой архива.")
    row_text = html.unescape(re.sub(r"<[^>]+>", " ", row_match.group(0)))
    hash_match = re.search(r"\b([0-9a-fA-F]{64})\b", row_text)
    if not hash_match:
        raise RuntimeError("На сайте Ventoy отсутствует SHA-256 архива; установка отменена.")
    expected = hash_match.group(1).lower()

    archive_href = None
    for href, label in re.findall(r"(?is)<a\b[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", page):
        if archive_name.lower() in html.unescape(label).lower() or archive_name.lower() in href.lower():
            archive_href = urllib.parse.urljoin(_VENTOY_PAGE, html.unescape(href))
            break
    if not archive_href:
        archive_href = f"https://sourceforge.net/projects/ventoy/files/v{version}/{archive_name}/download"
    archive_host = urllib.parse.urlparse(archive_href).hostname or ""
    if not _is_official_url(archive_href) and not archive_host.endswith("sourceforge.net"):
        raise RuntimeError("Ссылка на Ventoy ведёт не на официальный источник или SourceForge.")

    root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "JARVIS" / "tools" / "Ventoy" / version
    exe = root / "Ventoy2Disk.exe"
    if exe.is_file():
        return exe
    archive = root.parent / f"{archive_name}"
    _download(archive_href, archive, 150 * 1024**2, timeout=120,
              extra_hosts=("sourceforge.net", "downloads.sourceforge.net"))
    if _sha256(archive) != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError("SHA-256 Ventoy не совпала с официальным значением; архив удалён.")
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for item in zf.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("Архив Ventoy содержит небезопасный путь; распаковка отменена.")
        zf.extractall(root)
    archive.unlink(missing_ok=True)
    found = next(root.rglob("Ventoy2Disk.exe"), None)
    if found is None:
        raise RuntimeError("В официальном архиве Ventoy не найден Ventoy2Disk.exe.")
    return found


def _install_and_copy(disk_number: int, snapshot: dict, iso: Path) -> str:
    current = _disk_snapshot(disk_number)
    for key in ("FriendlyName", "UniqueId", "Size"):
        if str(current.get(key)) != str(snapshot.get(key)):
            raise RuntimeError("USB-диск изменился после подтверждения. Ничего не записано.")
    if not iso.is_file():
        raise RuntimeError("ISO-файл больше не найден.")
    sidecar = iso.with_suffix(iso.suffix + ".sha256")
    if not sidecar.is_file() or _sha256(iso) != sidecar.read_text(encoding="ascii").strip().lower():
        raise RuntimeError("ISO не имеет действительной локальной SHA-256 проверки. Сначала загрузите его штатным действием.")
    metadata_path = iso.with_suffix(iso.suffix + ".source.json")
    if not metadata_path.is_file():
        raise RuntimeError("Не найдена запись об источнике ISO. Повторно загрузите образ штатным действием.")

    exe = _ventoy_package()
    workdir = exe.parent
    for stale in (workdir / "cli_done.txt", workdir / "cli_log.txt", workdir / "cli_percent.txt"):
        stale.unlink(missing_ok=True)
    ps_exe = str(exe).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'; "
        f"$p=Start-Process -FilePath '{ps_exe}' "
        f"-ArgumentList @('VTOYCLI','/I','/PhyDrive:{disk_number}') "
        f"-WorkingDirectory '{str(workdir).replace(chr(39), chr(39)*2)}' "
        "-Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    )
    elevated = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    done = workdir / "cli_done.txt"
    if elevated.returncode or not done.is_file() or done.read_text(encoding="utf-8", errors="replace").strip() != "0":
        detail = (elevated.stderr or elevated.stdout or "UAC отменён или Ventoy сообщил об ошибке.").strip()
        raise RuntimeError(f"Ventoy не завершил установку: {detail}")

    volume_script = (
        f"Get-Partition -DiskNumber {disk_number} -ErrorAction SilentlyContinue | "
        "Get-Volume -ErrorAction SilentlyContinue | "
        "Where-Object { $_.FileSystemLabel -eq 'Ventoy' -and $_.DriveLetter } | "
        "Select-Object -First 1 -ExpandProperty DriveLetter"
    )
    volume = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", volume_script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    ).stdout.strip().splitlines()
    if not volume:
        return f"Ventoy установлен на USB-диск № {disk_number}, но Windows пока не назначила букву разделу. ISO проверен и находится здесь: {iso}"
    destination = Path(volume[-1] + ":\\") / iso.name
    shutil.copy2(iso, destination)
    return f"Готово: Ventoy установлен на USB-диск № {disk_number}, а проверенный ISO скопирован в {destination}."


def bootable_usb(parameters: dict, **_kwargs) -> str:
    params = parameters or {}
    action = str(params.get("action") or "devices").strip().lower()
    try:
        if action in {"devices", "list_usb"}:
            return _display_disks()
        if action == "download_iso":
            return _download_iso(params)
        if action == "create":
            number = int(params.get("disk_number"))
            iso = Path(str(params.get("iso_path") or "")).expanduser().resolve()
            if iso.suffix.lower() != ".iso" or not iso.is_file():
                return "Укажите локальный ISO-файл. Сначала найдите официальный ISO и скачайте его с проверкой SHA-256."
            snapshot = _disk_snapshot(number)
            metadata_path = iso.with_suffix(iso.suffix + ".source.json")
            if not metadata_path.is_file():
                return "Не найдена запись об источнике ISO. Сначала загрузите образ через действие download_iso."
            size_gb = int(snapshot.get("Size") or 0) / (1024**3)
            if iso.stat().st_size + 512 * 1024**2 > int(snapshot.get("Size") or 0):
                return "На выбранной флешке недостаточно места для этого ISO. Выберите накопитель большего объёма."
            description = str(snapshot.get("FriendlyName") or "USB-накопитель")
            title = f"Создать загрузочную флешку на диске № {number}"
            detail = (
                f"Устройство: {description}, {size_gb:.1f} ГБ.\nISO: {iso.name}\n"
                "Ventoy полностью переразметит и очистит выбранный USB-диск. "
                "Продолжайте только если на нём нет нужных файлов. После подтверждения Windows может запросить права администратора."
            )
            source_info = json.loads(metadata_path.read_text(encoding="utf-8"))
            source_host = urllib.parse.urlparse(str(source_info.get("source_url") or "")).hostname or "неизвестный сайт"
            if not source_info.get("official_source"):
                detail += (
                    f"\nИсточник ISO: {source_host} (сторонний сайт). SHA-256 совпадает с опубликованной суммой, "
                    "но это не подтверждает подлинность или безопасность образа."
                )
            return request_confirmation(
                f"bootable_usb_{number}", title, detail,
                lambda: _install_and_copy(number, snapshot, iso),
            )
        return "Действия: devices (список USB), download_iso (скачать ISO с опубликованной SHA-256), create (создать носитель после подтверждения)."
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, zipfile.BadZipFile) as exc:
        return f"Не удалось подготовить загрузочную флешку: {exc}"


TOOL = {
    "name": "bootable_usb",
    "description": (
        "Creates bootable Windows/Linux USB media on Windows. First use web_search to find the "
        "requested operating system ISO and its official checksum; for Linux use SHA256SUMS, "
        "and for Windows use the SHA-256 published on Microsoft's official ISO page. Then call "
        "download_iso with the direct ISO URL and checksum source/value; it only accepts HTTPS "
        "URLs from official OS domains by default; if the user requests a third-party source, allow "
        "a public HTTPS URL only when allow_unofficial=true, "
        "and clearly identify it as third-party. Always verify a published SHA-256. Use devices "
        "to list USB disks, ask the user "
        "which exact disk to use, then call create with its disk_number and verified iso_path. "
        "create requires an on-screen confirmation naming the exact USB model, capacity, ISO, and "
        "third-party source status when applicable; "
        "it downloads Ventoy from its official page, verifies the published SHA-256, and installs "
        "it using Ventoy CLI. Installation erases the selected USB. Never select a disk on the "
        "user's behalf, never use a non-USB/system disk, and never bypass confirmation or UAC."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "devices | download_iso | create"},
            "iso_url": {"type": "STRING", "description": "Direct HTTPS ISO URL on an official domain, or a public HTTPS URL with allow_unofficial=true"},
            "checksum_url": {"type": "STRING", "description": "HTTPS URL to SHA256SUMS containing the exact ISO filename; official by default"},
            "expected_sha256": {"type": "STRING", "description": "Official Microsoft-published SHA-256 for the exact Windows ISO, if there is no SHA256SUMS file"},
            "allow_unofficial": {"type": "BOOLEAN", "description": "Set true only when the user explicitly requests a third-party source or no official image is available; the UI must identify the source before writing"},
            "filename": {"type": "STRING", "description": "ISO filename, normally inferred from iso_url"},
            "disk_number": {"type": "INTEGER", "description": "Exact USB disk number returned by devices, chosen by the user"},
            "iso_path": {"type": "STRING", "description": "Path returned by download_iso after checksum verification"},
        },
        "required": [],
    },
    "handler": bootable_usb,
}
