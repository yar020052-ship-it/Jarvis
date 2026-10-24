"""Control an explicitly ADB-authorized Android device over USB or Wi-Fi."""

from __future__ import annotations

import os
import re
import subprocess
import time
import defusedxml.ElementTree as ET
from pathlib import Path

# adb disambiguates two mDNS adverts that share a name by appending " (2)" to one
# of them, so a serial can contain a space and parentheses. ADB arguments are
# passed as a list (never through a shell), so accepting them here is safe.
_SERIAL_RE = re.compile(r"^[A-Za-z0-9._:()\- ]{1,128}$")
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")
_KEYS = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "recent": "KEYCODE_APP_SWITCH",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "volume_mute": "KEYCODE_VOLUME_MUTE",
    "power": "KEYCODE_POWER",
    "enter": "KEYCODE_ENTER",
    "delete": "KEYCODE_DEL",
    "space": "KEYCODE_SPACE",
    "tab": "KEYCODE_TAB",
}
_MAX_TEXT = 2000


def _adb_path() -> str:
    # Единый резолвер (core/adb.py): JARVIS_ADB_PATH → PATH → PATH.exe.
    # Держали отдельную логику — теперь одна на все модули.
    from core.adb import find_tool

    adb = find_tool("adb")
    if not adb:
        raise RuntimeError(
            "ADB не найден. Установите Android SDK Platform-Tools и добавьте adb в PATH "
            "или задайте переменную JARVIS_ADB_PATH. См. docs/android_setup_ru.md."
        )
    return adb


def _run(args: list[str], serial: str | None = None, timeout: int = 20, _retries: int = 2) -> str:
    command = [_adb_path()]
    if serial:
        command += ["-s", serial]
    command += args
    # CREATE_NO_WINDOW: без него на Windows каждая команда adb мигает
    # консольным окном (android_device._run это уже делал).
    _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=_flags,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ADB не ответил за {timeout} секунд.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        low = (detail or "").lower()
        # Wireless debug links drop under load ("device offline"). Bringing the
        # device back once and retrying avoids turning a connection blip into a
        # failed unlock/tap in the middle of a workflow. Only safe (read-only)
        # commands are retried: retrying an injected `input text`/`tap`/keyevent
        # after a drop could DOUBLE-apply it (e.g. enter the PIN twice or toggle
        # power back on).
        mutating = (
            args[0] == "install"
            or (
                args[0] == "shell"
                and args[1] in {"input", "settings", "monkey", "pm", "am"}
            )
        )
        if (
            serial
            and not mutating
            and _retries > 0
            and any(
                token in low for token in ("offline", "no devices", "not found", "closed")
            )
        ):
            try:
                subprocess.run(
                    [_adb_path(), "reconnect"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except Exception:
                pass
            time.sleep(1.0)
            return _run(args, serial, timeout, _retries - 1)
        raise RuntimeError(detail or f"ADB завершился с кодом {result.returncode}.")
    return result.stdout.strip()


# `adb devices` prints "<serial> <state> [<details>]". A serial may contain a
# space (see _SERIAL_RE), so split on the first known state token instead of on
# every whitespace run — otherwise a device reported as "foo (2)" is silently lost.
_ADB_STATES = (
    "device", "offline", "unauthorized", "bootloader", "sideload", "recovery",
    "rescue", "host", "connecting", "authorizing", "no permissions",
    "fastboot", "fastbootd",
)
_DEVICE_LINE_RE = re.compile(
    r"^(?P<serial>\S.*?)\s+(?P<state>" + "|".join(_ADB_STATES) + r")\b(?P<detail>.*)$"
)


def _devices() -> list[tuple[str, str]]:
    output = _run(["devices", "-l"])
    found = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        match = _DEVICE_LINE_RE.match(line)
        if match and match.group("state") == "device":
            found.append((match.group("serial").strip(), match.group("detail").strip()))
    return found


def _launcher_activity(resolved: str) -> str:
    """Pull the component name out of `cmd package resolve-activity --brief`.

    Output looks like:
        priority=0 preferredOrder=0 ...
        com.jarvis.remote/.MainActivity
    so the component is the only line with a slash and no spaces.
    """
    for raw in (resolved or "").splitlines():
        line = raw.strip()
        if line.startswith("priority=") or not line or " " in line or "/" not in line:
            continue
        return line
    return ""


def _installed_packages(serial: str) -> set[str]:
    try:
        out = _run(["shell", "pm", "list", "packages"], serial, timeout=30)
    except RuntimeError:
        return set()
    return {
        line.split("package:", 1)[1].strip()
        for line in (out or "").splitlines()
        if line.startswith("package:")
    }


_DEVICE_WAIT = 15.0  # how long to wait for a tablet that just dropped the link


def _harden_connection(serial: str) -> None:
    """Best-effort: keep the wireless debug link alive while the screen is off.

    When the screen turns off the device may enter Doze, which suspends mDNS and
    drops the adb connection, so a later `unlock` would find it offline. These
    toggles are idempotent and safe to run on every unlock session.
    """
    try:
        _run(["shell", "dumpsys", "deviceidle", "disable"], serial, timeout=10)
    except RuntimeError:
        pass
    try:
        _run(["shell", "settings", "put", "global", "wifi_sleep_policy", "2"], serial, timeout=10)
    except RuntimeError:
        pass
    # While charging, "stay awake" keeps Wi-Fi (and thus the ADB link) alive even
    # with the screen off — the strongest fix when the device is plugged in.
    try:
        battery = _run(["shell", "dumpsys", "battery"], serial, timeout=10)
        if re.search(r"(AC|USB|Wireless) powered: true", battery):
            _run(["shell", "svc", "power", "stayon", "usb"], serial, timeout=10)
    except RuntimeError:
        pass


def _ident_token(serial: str) -> tuple[str, str]:
    """Split an ADB serial into (mdns_name, ip); usually only one is non-empty.

    ADB reports the same wireless device either as
    ``adb-<name>._adb-tls-connect._tcp`` or as ``<ip>:<port>`` depending on how
    it connected, so both forms must map to the same physical device.
    """
    s = (serial or "").strip()
    s = re.sub(r"\._adb-tls-connect\._tcp$", "", s)
    s = re.sub(r"\._adb\._tcp$", "", s)
    # adb's " (2)" suffix only disambiguates duplicate mDNS names; it is not part
    # of the device identity, so drop it before comparing two serials.
    s = re.sub(r"\s*\(\d+\)$", "", s).strip()
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}", s):
        return "", s.split(":")[0]
    return s, ""


def _mdns_index() -> tuple[dict[str, str], dict[str, str]]:
    """(name -> endpoint, ip -> name) from the current mDNS advertisements."""
    names: dict[str, str] = {}
    ips: dict[str, str] = {}
    for item in _adb_mdns_devices():
        name, endpoint = item.get("name", ""), item.get("endpoint", "")
        if not endpoint:
            continue
        names[name] = endpoint
        ips[endpoint.split(":")[0]] = name
    return names, ips


def _find_connected(requested: str) -> str | None:
    """Return the connected serial that is the same physical device as `requested`."""
    serials = [serial for serial, _ in _devices()]
    if requested in serials:
        return requested
    req_name, req_ip = _ident_token(requested)
    names, ips = _mdns_index()
    cand_names = {req_name} if req_name else set()
    cand_ips = {req_ip} if req_ip else set()
    if req_ip and req_ip in ips:
        cand_names.add(ips[req_ip])
    if req_name and req_name in names:
        cand_ips.add(names[req_name].split(":")[0])
    for serial in serials:
        name, ip = _ident_token(serial)
        if not ip and name and name in names:
            ip = names[name].split(":")[0]
        if not name and ip and ip in ips:
            name = ips[ip]
        if (ip and ip in cand_ips) or (name and name in cand_names):
            return serial
    return None


def _same_physical(a: str, b: str) -> bool:
    """True when two ADB serials (mDNS name and/or ip:port) are one device."""
    if a == b:
        return True
    an, ai = _ident_token(a)
    bn, bi = _ident_token(b)
    names, _ = _mdns_index()
    if not ai and an and an in names:
        ai = names[an].split(":")[0]
    if not bi and bn and bn in names:
        bi = names[bn].split(":")[0]
    if ai and bi:
        return ai == bi
    if an and bn:
        return an == bn
    return False


def _reconnect_via_mdns(requested: str) -> None:
    """Best-effort: `adb connect` the mDNS endpoint of the requested device.

    This recovers the wireless link after the screen turned off *without*
    needing the user to press the power button.
    """
    req_name, req_ip = _ident_token(requested)
    names, ips = _mdns_index()
    endpoints: set[str] = set()
    if req_name and req_name in names:
        endpoints.add(names[req_name])
    if req_ip and req_ip in ips:
        endpoints.add(names[ips[req_ip]])
    for name, endpoint in names.items():
        if req_name and (req_name in name or name in req_name):
            endpoints.add(endpoint)
    for endpoint in endpoints:
        try:
            _run(["connect", endpoint], timeout=15)
        except RuntimeError:
            pass


def _reconnect_all_mdns() -> None:
    """Best-effort: `adb connect` every Android device advertising via mDNS.

    Unpaired devices simply refuse the connection, so this is safe to run when
    no device resolves by name.
    """
    for item in _adb_mdns_devices():
        endpoint = item.get("endpoint", "")
        if not endpoint:
            continue
        try:
            _run(["connect", endpoint], timeout=15)
        except RuntimeError:
            pass


def _select_device(requested: str = "") -> str:
    devices = _devices()
    if requested:
        if not _SERIAL_RE.fullmatch(requested):
            raise RuntimeError("Некорректный serial устройства.")
        # Accept a friendly name ("Redmi", "RT2", "OUKITEL") as well as a strict
        # serial. A voice command like "выключи экран на редми" reaches the model
        # as a name; without this the selector would spin waiting for a serial
        # that is really a name and then declare the device unreachable. This is
        # the same matching speak_on_device._resolve_target uses.
        if not _find_connected(requested):
            frag = requested.lower()
            matches: list[str] = []
            for serial, detail in devices:
                if frag in serial.lower() or frag in detail.lower():
                    if not any(_same_physical(serial, kept) for kept in matches):
                        matches.append(serial)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise RuntimeError(
                    f"Под «{requested}» подходит несколько устройств: "
                    + ", ".join(matches)
                    + ". Уточните device_id."
                )
        # The wireless link drops when the screen turns off, but the device keeps
        # announcing itself via mDNS: re-`adb connect` it instead of asking the
        # user to press the power button. The advertised port changes on each
        # reconnect, so retry against fresh mDNS data every round.
        deadline = time.monotonic() + _DEVICE_WAIT
        while time.monotonic() < deadline:
            _reconnect_via_mdns(requested)
            found = _find_connected(requested)
            if found:
                return found
            time.sleep(2.0)
        raise RuntimeError(
            f"Устройство {requested} не отвечает по ADB. Убедитесь, что включены Wi-Fi и "
            "«Беспроводная отладка» на устройстве; если оно в глубоком сне, разбудите его "
            "(кнопка питания) и повторите."
        )
    if not devices:
        deadline = time.monotonic() + _DEVICE_WAIT
        while time.monotonic() < deadline:
            _reconnect_all_mdns()
            devices = _devices()
            if devices:
                break
            time.sleep(2.0)
        if not devices:
            output = _run(["devices", "-l"])
            if "unauthorized" in output:
                raise RuntimeError("Разрешите отладку по USB на экране устройства и повторите команду.")
            raise RuntimeError(
                "Android-устройства не найдены в ADB. Включите Wi-Fi и «Беспроводную отладку» "
                "на устройстве или подключите его по USB."
            )
    if len(devices) > 1:
        # The same wireless device can appear twice (once as its mDNS serial and
        # once as ip:port). Collapse aliases before declaring it ambiguous.
        unique: list[str] = []
        for serial, _ in devices:
            if not any(_same_physical(serial, kept) for kept in unique):
                unique.append(serial)
        if len(unique) == 1:
            return unique[0]
        # Несколько физических устройств и явный device_id не задан:
        # используем сохранённый выбор пользователя, если он резолвится.
        if not requested:
            try:
                from memory.config_manager import get_android_voice_device

                _pref = (get_android_voice_device() or "").strip()
            except Exception:
                _pref = ""
            if _pref:
                try:
                    _hit = _find_connected(_pref)
                except Exception:
                    _hit = None
                if _hit:
                    return _hit
        choices = ", ".join(unique)
        raise RuntimeError(f"Подключено несколько устройств ({choices}). Укажите device_id.")
    return devices[0][0]


def _adb_mdns_devices() -> list[dict]:
    """Android wireless-debugging devices broadcasting on mDNS, not yet paired.

    Each entry: {"name": ..., "endpoint": "ip:port", "paired": bool}. These are
    *seen* on the LAN even before this PC has paired with them.
    """
    services: list[dict] = []
    try:
        out = _run(["mdns", "services"], timeout=10)
    except RuntimeError:
        return services
    paired_ips: set[str] = set()
    paired_names: set[str] = set()
    for serial, _ in _devices():
        name, ip = _ident_token(serial)
        if ip:
            paired_ips.add(ip)
        if name:
            paired_names.add(name)
    known = {"_adb-tls-connect._tcp", "_adb._tcp"}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        # The service name may itself contain spaces ("adb-<sn> (2)"), so read the
        # type and endpoint from the right and treat the rest as the name.
        endpoint, srv_type = parts[-1], parts[-2]
        name = " ".join(parts[:-2])
        if srv_type not in known:
            continue
        # The same device may be connected under its mDNS serial or as ip:port.
        # Its advertised name can carry adb's " (2)" duplicate suffix, so compare
        # the normalised token as well or the phone looks "not yet connected".
        already = (
            name in paired_names
            or _ident_token(name)[0] in paired_names
            or endpoint.split(":")[0] in paired_ips
        )
        services.append(
            {
                "name": name,
                "endpoint": endpoint,
                "paired": already,
            }
        )
    return services


def _unique_devices() -> list[tuple[str, str]]:
    """`_devices()` without aliases: one entry per physical device.

    A wireless device is usually connected twice — once under its mDNS name and
    once as ip:port — which otherwise shows up as two identical devices in the UI
    and in the device list. The mDNS-name alias is preferred because it survives
    the advertised port changing.
    """
    names, _ = _mdns_index()
    unique: dict[str, tuple[str, str]] = {}
    for serial, extra in _devices():
        mdns_name, ip = _ident_token(serial)
        if not ip and mdns_name and mdns_name in names:
            ip = names[mdns_name].split(":")[0]
        identity = ip or mdns_name or serial
        kept = unique.get(identity)
        if kept is None or (mdns_name and not _ident_token(kept[0])[0]):
            unique[identity] = (serial, extra)
    return list(unique.values())


def _adb_all_devices() -> str:
    rows = []
    devices = _unique_devices()
    if devices:
        rows.append("Подключено устройств ADB (состояние device — авторизовано):")
        for serial, extra in devices:
            rows.append(f"• {serial}  {extra}")
    else:
        rows.append("Подключённых ADB-устройств нет.")
    discovered = [item for item in _adb_mdns_devices() if not item["paired"]]
    if discovered:
        rows.append("")
        rows.append("Android-устройства, видимые в Wi-Fi, но ещё не подключённые:")
        for item in discovered:
            rows.append(
                f"• {item['name']} — {item['endpoint']} "
                "(чтобы управлять — назови JARVIS wifi_pair endpoint и код с экрана)"
            )
    else:
        rows.append("")
        rows.append("Новых Android-устройств с беспроводной отладкой в Wi-Fi не обнаружено.")
    return "\n".join(rows)


def device_snapshot() -> list[dict]:
    """Read-only status of every ADB-connected device, for the desktop UI.

    One dict per connected (authorized) device::

        {"serial", "name", "model", "product", "device", "brand",
         "manufacturer", "battery": int|None, "charging": bool,
         "screen_on": bool | None}

    ``name`` is a best-effort friendly label (market name → model → brand ->
    mDNS name). Never raises: ADB missing or a device dropping mid-read simply
    yields fewer devices / ``None`` fields so the caller can always render.
    These are read-only commands (no `input`/`settings`), so the retry path in
    ``_run`` that would reconnect a dropped link stays disabled on purpose.
    """
    try:
        devices = _unique_devices()
    except Exception:
        return []

    snapshot: list[dict] = []
    for serial, extra in devices:
        # `adb devices -l` already carries product/model/device; getprop adds the
        # brand (Xiaomi's "Redmi"/"POCO" is only there) and the marketing name.
        extras: dict[str, str] = {}
        for token in (extra or "").split():
            if ":" in token:
                key, _, value = token.partition(":")
                extras[key] = value
        props: dict[str, str] = {}
        try:
            raw = _run(["shell", "getprop"], serial, timeout=8, _retries=0)
            props = dict(re.findall(r"\[([^\]]+)\]:\s*\[([^\]]*)\]", raw))
        except RuntimeError:
            props = {}

        mdns_name, ip = _ident_token(serial)
        model = props.get("ro.product.model") or extras.get("model", "")
        product = props.get("ro.product.product") or extras.get("product", "")
        device = props.get("ro.product.device") or extras.get("device", "")
        brand = props.get("ro.product.brand", "")
        manufacturer = props.get("ro.product.manufacturer", "")
        market = (
            props.get("ro.product.marketname")
            or props.get("ro.product.odm.marketname")
            or props.get("ro.product.vendor.marketname")
            or ""
        )
        name = market or model or brand or mdns_name or ip or serial

        battery: int | None = None
        charging = False
        try:
            raw = _run(["shell", "dumpsys", "battery"], serial, timeout=8, _retries=0)
            match = re.search(r"level:\s*(\d+)", raw)
            if match:
                battery = int(match.group(1))
            charging = bool(re.search(r"(?:AC|USB|Wireless) powered:\s*true", raw))
        except RuntimeError:
            pass

        screen_on: bool | None = None
        try:
            raw = _run(["shell", "dumpsys", "power"], serial, timeout=8, _retries=0)
            match = re.search(r"mWakefulness=(\w+)", raw)
            if match:
                screen_on = match.group(1) == "Awake"
        except RuntimeError:
            pass

        snapshot.append(
            {
                "serial": serial,
                "name": name,
                "model": model,
                "product": product,
                "device": device,
                "brand": brand,
                "manufacturer": manufacturer,
                "battery": battery,
                "charging": charging,
                "screen_on": screen_on,
            }
        )
    return snapshot


def _wifi_pair(endpoint: str, code: str) -> str:
    """Pair this PC with an Android device's wireless-debugging pairing code."""
    endpoint = endpoint.strip()
    code = code.strip()
    if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}", endpoint):
        raise RuntimeError(
            "Укажите IPv4:порт кода сопряжения из меню Android Wireless debugging, "
            "например 192.168.1.20:37123."
        )
    if not re.fullmatch(r"\d{1,6}", code):
        raise RuntimeError("Код сопряжения — 6 цифр с экрана устройства.")
    output = _run(["pair", endpoint, code], timeout=20)
    if "successfully paired" not in output.lower():
        raise RuntimeError(f"Сопряжение не удалось. Ответ ADB: {output[:500]}")
    # After pairing the daemon should pick the device up via mDNS; nudge it and
    # connect to any newly-visible -tls endpoint so `list_devices` shows it.
    try:
        _run(["reconnect"], timeout=10)
    except RuntimeError:
        pass
    time.sleep(1.5)
    for item in _adb_mdns_devices():
        if not item["paired"]:
            try:
                _run(["connect", item["endpoint"]], timeout=15)
            except RuntimeError:
                pass
    return (
        f"Сопряжение с {endpoint} выполнено. Устройство появится в list_devices; "
        "если потребуется PIN разблокировки — сохрани его через set_unlock_password."
    )


def _wifi_connect(endpoint: str) -> str:
    endpoint = endpoint.strip()
    match = re.fullmatch(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})", endpoint)
    if not match:
        raise RuntimeError("Укажите IPv4:порт из меню Android Wireless debugging, например 192.168.1.20:41235.")
    octets = [int(part) for part in match.group(1).split(".")]
    port = int(match.group(2))
    if any(octet > 255 for octet in octets) or not 1 <= port <= 65535:
        raise RuntimeError("Некорректный IPv4-адрес или порт.")
    host = match.group(1)
    if not (host.startswith("10.") or host.startswith("192.168.") or
            (host.startswith("172.") and 16 <= octets[1] <= 31) or host.startswith("169.254.")):
        raise RuntimeError("Разрешено подключение только к адресу локальной сети.")
    result = _run(["connect", endpoint], timeout=15)
    if "connected to" in result.lower() or "already connected" in result.lower():
        return f"ADB подключился по Wi-Fi: {result}. Теперь проверь android_control action=status."
    raise RuntimeError(f"ADB не подтвердил Wi-Fi подключение: {result}")


def _brightness(serial: str) -> str:
    level = _run(["shell", "settings", "get", "system", "screen_brightness"], serial)
    mode = _run(["shell", "settings", "get", "system", "screen_brightness_mode"], serial)
    try:
        percent = round(max(0, min(255, int(level))) * 100 / 255)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Android не сообщил текущую яркость экрана.") from exc
    mode_text = "автояркость включена" if mode == "1" else "ручная яркость"
    return f"Яркость экрана: {percent}% (значение {level}/255; {mode_text})."


def _set_brightness(serial: str, value: object) -> str:
    try:
        percent = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Яркость задаётся целым числом от 0 до 100%.") from exc
    if not 0 <= percent <= 100:
        raise RuntimeError("Яркость должна быть от 0 до 100%.")
    level = round(percent * 255 / 100)
    _run(["shell", "settings", "put", "system", "screen_brightness_mode", "0"], serial)
    _run(["shell", "settings", "put", "system", "screen_brightness", str(level)], serial)
    actual = _run(["shell", "settings", "get", "system", "screen_brightness"], serial)
    if actual != str(level):
        raise RuntimeError(f"Android не подтвердил яркость: запрошено {level}/255, получено {actual}.")
    return f"Яркость экрана установлена на {percent}% ({level}/255). Автояркость выключена; включить её можно командой автояркости."


# ── Unlock: the owner's own device, the code they would type by hand ─────────
# The user enabled ADB and stored the tablet PIN/password (config
# `tablet_unlock_password`). These helpers type it on the lock screen exactly
# the way the user would. Password material is never echoed back in replies.
def _screen_size(serial: str) -> tuple[int, int]:
    out = _run(["shell", "wm", "size"], serial, timeout=10)
    match = re.search(r"Physical size:\s*(\d+)x(\d+)", out)
    if not match:
        raise RuntimeError("Не удалось определить размер экрана планшета.")
    return int(match.group(1)), int(match.group(2))


def _is_locked(serial: str) -> bool | None:
    """True = keyguard showing, False = unlocked, None = could not tell.

    `mDreamingLockscreen` is the SCREENSAVER flag, not the lock state: it flips
    to false the moment the keyguard is woken, so it can only ever say "locked",
    never "unlocked". The authoritative signal is `dumpsys trust` → deviceLocked.
    """
    # 1) Trust service literally reports whether the device is locked.
    try:
        trust = _run(["shell", "dumpsys", "trust"], serial, timeout=15)
        match = re.search(r"deviceLocked=(\d)", trust)
        if match:
            return match.group(1) == "1"
    except RuntimeError:
        pass

    try:
        out = _run(["shell", "dumpsys", "window"], serial, timeout=20)
    except RuntimeError:
        out = ""
    # 2) Modern keyguard-orchestrator flags, then the legacy pair.
    for pattern in (
        r"isKeyguardShowing=(true|false)",
        r"mKeyguardShowing=(true|false)",
        r"mShowingLockscreen=(true|false)",
    ):
        match = re.search(pattern, out)
        if match:
            return match.group(1) == "true"
    # 3) Input restriction while a window is focused is a strong lock signal.
    if re.search(r"mInputRestricted=true", out):
        return True
    # 4) Legacy dream flag — only 'true' is trustworthy.
    match = re.search(r"mDreamingLockscreen=(true|false)", out)
    if match and match.group(1) == "true":
        return True
    if re.search(r"mCurrentFocus=.*?Keyguard", out):
        return True
    return None


def _unlock_sequence(serial: str, password: str) -> str:
    """Wake the tablet, reveal the PIN pad, type the stored code, verify."""
    if not password:
        raise RuntimeError(
            "Пароль планшета не сохранён. Сохраните его через action=set_unlock_password "
            "(параметр password) или вручную в config/api_keys.json → tablet_unlock_password."
        )
    if not 4 <= len(password) <= 63:
        raise RuntimeError("Пароль планшета должен быть длиной от 4 до 63 символов.")
    if not password.isascii():
        raise RuntimeError(
            "ADB вводит только печатный ASCII. Пин-код из цифр подойдёт; "
            "пароль с не-латинскими символами после типа «грнф» — нет."
        )

    # Keep the wireless link alive while the screen is off so the unlock's own
    # steps (and later actions) do not hit a Doze-dropped connection.
    _harden_connection(serial)

    # Never type into an already-unlocked UI: if the keyguard is not showing,
    # there is nothing to unlock and injected digits could land in an app.
    state = _is_locked(serial)
    if state is False:
        return "Планшет уже разблокирован — пароль не вводился."
    if state is None:
        raise RuntimeError(
            "Не удалось определить, заблокирован ли планшет. Сначала осмотри его "
            "через observe_screen/inspect_screen и повтори unlock только если он заблокирован."
        )

    _run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial, timeout=10)
    time.sleep(0.6)
    state = _is_locked(serial)
    if state is False:
        return "Планшет уже разблокирован — пароль не вводился."
    if state is None:
        raise RuntimeError(
            "Не удалось подтвердить блокировку после пробуждения экрана. "
            "Осмотри планшет через observe_screen и повтори unlock только если он заблокирован."
        )

    # A swipe up reveals the PIN pad on most stock launchers. On devices that
    # keep the pad visible it is a harmless no-op, so we tolerate failure.
    try:
        width, height = _screen_size(serial)
        _run(
            [
                "shell", "input", "swipe",
                str(width // 2), str(int(height * 0.85)),
                str(width // 2), str(int(height * 0.22)),
                "350",
            ],
            serial,
            timeout=15,
        )
    except RuntimeError:
        pass
    time.sleep(0.8)

    escaped = password.replace("%", "%25").replace(" ", "%s")
    _run(["shell", "input", "text", escaped], serial, timeout=15)
    time.sleep(0.4)
    # A short numeric PIN may have auto-submitted already; a dropped wireless
    # link on ENTER must not abort an otherwise successful unlock.
    try:
        _run(["shell", "input", "keyevent", "KEYCODE_ENTER"], serial, timeout=10)
    except RuntimeError:
        pass
    time.sleep(1.2)

    locked = _is_locked(serial)
    if locked is None:
        # The link can blip right after the injection; re-resolve the device
        # (mDNS reconnect) and try the state check once more before giving up.
        for _ in range(2):
            try:
                serial = _select_device(serial)
            except RuntimeError:
                break
            time.sleep(1.0)
            locked = _is_locked(serial)
            if locked is not None:
                break
    if locked is True:
        return (
            "Пароль введён, но планшет всё ещё заблокирован. Вероятно, сохранённый "
            "пароль неверен — обновите его через action=set_unlock_password."
        )
    if locked is False:
        return "Планшет разблокирован."
    return (
        "Пароль введён и отправлен. Точное состояние экрана проверить не удалось — "
        "осмотри свежий кадр планшета через observe_screen."
    )


def _lock_tablet(serial: str) -> str:
    """Turn the screen off through the power key (locks the device)."""
    _run(["shell", "input", "keyevent", "KEYCODE_POWER"], serial, timeout=10)
    return "Экран планшета выключен и заблокирован."


def _set_unlock_password(password: str, device_id: str = "") -> str:
    from memory.config_manager import save_tablet_unlock_password

    if password == "":
        save_tablet_unlock_password("", device_id)
        return "Пароль устройства удалён из конфигурации."
    if not 4 <= len(password) <= 63:
        raise RuntimeError("Пароль должен быть длиной от 4 до 63 символов.")
    if not password.isascii():
        raise RuntimeError("Пароль должен состоять из печатных ASCII-символов (ADB).")
    save_tablet_unlock_password(password, device_id)
    where = f" для устройства {device_id}" if device_id.strip() else " по умолчанию"
    return (
        f"Пароль{where} сохранён локально в config/api_keys.json. "
        "Никогда не произноси и не повторяй его в ответе."
    )


def _tablet_frame(serial: str = "") -> bytes | None:
    """Capture the authorized tablet's screen as PNG bytes, or None on any failure.

    This is what gives JARVIS "eyes" on the tablet: after each tap/swipe/unlock
    the fresh frame can be attached to the conversation so the model works from
    the real screen instead of assumptions. Safe from the UI thread (blocking
    ADB read of at most ~30 s).
    """
    try:
        sid = _select_device(serial.strip()) if serial.strip() else _select_device("")
    except RuntimeError:
        return None
    adb = _adb_path()
    try:
        result = subprocess.run(
            [adb, "-s", sid, "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return None
    if result.returncode or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return result.stdout


def _notification_records(serial: str) -> int | None:
    """Count active notifications straight from Android, or None if unsupported.

    `cmd notification list` prints one "<user>|<package>|..." line per record;
    it is the same counter the system shade shows, so before/after numbers from
    here are real evidence — not a guess made from a screenshot.
    """
    try:
        out = _run(["shell", "cmd", "notification", "list"], serial, timeout=15)
    except RuntimeError:
        return None
    return len([ln for ln in (out or "").splitlines() if re.match(r"^\d+\|", ln.strip())])


def _shade_rows(root: ET.Element | None) -> int:
    """How many notification rows the dumped shade shows (-1 = no dump)."""
    if root is None:
        return -1
    return sum(
        1 for node in root.iter("node")
        if (node.attrib.get("resource-id") or "").endswith("expandableNotificationRow")
    )


def _ui_root(serial: str, _retries: int = 1) -> ET.Element | None:
    """Свежий дамп интерфейса или None.

    Файл на устройстве удаляется перед дампом: `uiautomator dump` умеет молча
    НЕ перезаписывать старый (экран не в покое, анимация), и тогда `cat` вернул
    бы состояние прошлого экрана — по такой «устаревшей картинке» нельзя ни
    нажимать, ни учиться. Если дампа нет — честное None (и одна повторная
    попытка, чтобы переждать анимацию).
    """
    remote = "/sdcard/window_dump.xml"
    for attempt in range(_retries + 1):
        try:
            _run(["shell", "rm", "-f", remote], serial, timeout=10)
            _run(["shell", "uiautomator", "dump", remote], serial, timeout=30)
            xml = _run(["exec-out", "cat", remote], serial, timeout=20)
            return ET.fromstring(xml)
        except (RuntimeError, ET.ParseError):
            if attempt < _retries:
                time.sleep(1.0)
    return None


def _bounds_center(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", (value or "").strip())
    if not match:
        return None
    x1, y1, x2, y2 = (int(g) for g in match.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def _screen_signature(root: ET.Element) -> set:
    """Отпечаток «что сейчас на экране»: подписи и bounds всех узлов дампа."""
    signature = set()
    for node in root.iter("node"):
        label = (node.attrib.get("text") or "").strip() or (node.attrib.get("content-desc") or "").strip()
        rid = (node.attrib.get("resource-id") or "").strip()
        if label or rid:
            signature.add(((label or rid)[:80], node.attrib.get("bounds", "")))
    return signature


def _signature_overlap(left: set, right: set) -> float:
    """Jaccard двух отпечатков: 1.0 — тот же экран; ниже — экран заметно другой."""
    if not left or not right:
        return 1.0
    return len(left & right) / len(left | right)


# SystemUI names its clear-all control differently across ROMs and locales. The
# AOSP footer id (dismiss_text) is tried first: some ROMs also render a decoy
# "btn_clear_all" that clears nothing (seen on the OUKITEL RT2), so it is the
# last resort — every tap is verified against the notification counter anyway.
_CLEAR_ALL_ID_PRIORITY = (
    "dismiss_text", "dismiss_button", "clear_all", "clear_all_button",
    "clear_all_text", "btn_clear_all",
)
_CLEAR_ALL_LABELS = {
    "clear all", "clear all notifications", "dismiss all", "dismiss all notifications",
    "очистить все", "очистить всё", "очистить все уведомления", "удалить все",
    "удалить все уведомления", "стереть все уведомления",
}


def _clear_all_candidates(root: ET.Element) -> list[tuple[str, tuple[int, int]]]:
    """Every plausible "Clear all" control in the shade dump, best first.

    Returns (key, center) pairs: resource ids ranked by _CLEAR_ALL_ID_PRIORITY,
    then exact label matches. A node is tapped only if it (or a close ancestor)
    is clickable, and near-identical centers are collapsed so one button is not
    queued twice.
    """
    parents = {child: parent for parent in root.iter() for child in parent}

    def climb_center(node):
        current = node
        for _ in range(3):
            if current.attrib.get("clickable") == "true":
                center = _bounds_center(current.attrib.get("bounds", ""))
                if center:
                    return center
            parent = parents.get(current)
            if parent is None:
                break
            current = parent
        return _bounds_center(node.attrib.get("bounds", ""))

    ranked: list[tuple[tuple[int, int], str, tuple[int, int]]] = []
    for node in root.iter("node"):
        short = (node.attrib.get("resource-id") or "").split("/")[-1].lower()
        text = (node.attrib.get("text") or "").strip().lower()
        desc = (node.attrib.get("content-desc") or "").strip().lower()
        if short in _CLEAR_ALL_ID_PRIORITY:
            rank, key = (0, _CLEAR_ALL_ID_PRIORITY.index(short)), short
        elif text in _CLEAR_ALL_LABELS or desc in _CLEAR_ALL_LABELS:
            rank, key = (1, 0), text or desc
        else:
            continue
        center = climb_center(node)
        if center:
            ranked.append((rank, key, center))
    ranked.sort(key=lambda item: item[0])
    out: list[tuple[str, tuple[int, int]]] = []
    for _, key, center in ranked:
        if all(abs(center[0] - c[0]) > 40 or abs(center[1] - c[1]) > 40 for _, c in out):
            out.append((key, center))
    return out


def _collapse_shade(serial: str) -> None:
    try:
        _run(["shell", "cmd", "statusbar", "collapse"], serial, timeout=10)
    except RuntimeError:
        pass


def _open_shade(serial: str) -> ET.Element | None:
    """Bring the notification shade on screen and dump its hierarchy.

    `cmd statusbar expand-notifications` is refused on some ROMs and sometimes
    silently does nothing, so a top-edge swipe is the fallback. Success is
    judged by the dump containing notification rows, never by exit codes.
    """
    try:
        _run(["shell", "cmd", "statusbar", "expand-notifications"], serial, timeout=10)
        time.sleep(1.5)
        root = _ui_root(serial)
    except RuntimeError:
        root = None
    if _shade_rows(root) > 0:
        return root
    try:
        width, height = _screen_size(serial)
        _run(
            ["shell", "input", "swipe", str(width // 2), "4",
             str(width // 2), str(int(height * 0.6)), "300"],
            serial, timeout=15,
        )
        time.sleep(1.8)
    except RuntimeError:
        pass
    return _ui_root(serial)


def _clear_notifications(serial: str) -> str:
    """Open the shade, tap the real «Clear all», and verify what actually happened.

    Every tap is confirmed by Android's own notification counter: if the first
    button changes nothing (some ROMs render a decoy "clear all" control), the
    next candidate is tried. The reply quotes verified numbers or states the
    failure — it can never claim a success that the device did not confirm.
    """
    try:
        awake = "Awake" in _run(["shell", "dumpsys", "power"], serial, timeout=15)
    except RuntimeError:
        awake = True
    woke = False
    if not awake:
        try:
            _run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial, timeout=10)
            woke = True
            time.sleep(0.8)
        except RuntimeError:
            pass

    def finish(message: str) -> str:
        if woke:  # restore the screen state we changed on the way in
            try:
                _run(["shell", "input", "keyevent", "KEYCODE_SLEEP"], serial, timeout=10)
            except RuntimeError:
                pass
        return message

    if _is_locked(serial) is True:
        return finish(
            "Планшет заблокирован — шторку уведомлений открыть нельзя, уведомления не тронуты. "
            "Разблокируй планшет (action=unlock или вручную) и повтори очистку."
        )

    before = _notification_records(serial)
    if before == 0:
        return finish("Активных уведомлений на планшете нет — чистить нечего.")

    tried: set[str] = set()
    saw_candidates = False
    for _attempt in range(2):
        root = _open_shade(serial)
        candidates = _clear_all_candidates(root) if root is not None else []
        if not candidates:
            # The footer can appear a beat after the shade finishes animating.
            time.sleep(1.2)
            root = _ui_root(serial)
            candidates = _clear_all_candidates(root) if root is not None else []
        pick = next((item for item in candidates if item[0] not in tried), None)
        if pick is None:
            _collapse_shade(serial)
            break
        saw_candidates = True
        tried.add(pick[0])
        center = pick[1]
        _run(["shell", "input", "tap", str(center[0]), str(center[1])], serial, timeout=10)
        time.sleep(1.0)
        _collapse_shade(serial)
        time.sleep(0.8)

        after = _notification_records(serial)
        if after is None:
            # This ROM hides the counter: reopen the shade and use the visible
            # rows as the evidence instead.
            rows = _shade_rows(_open_shade(serial))
            _collapse_shade(serial)
            if rows == 0:
                return finish("Готово: нажал «Очистить все», шторка пуста — уведомления очищены (проверено по экрану).")
            if rows > 0:
                return finish(f"Часть уведомлений осталась: после нажатия в шторке видно {rows} шт.")
            return finish("Нажал «Очистить все», но подтвердить результат не удалось — проверь шторку планшета.")
        if before is not None and after < before:
            if after == 0:
                return finish(f"Готово: уведомления очищены — было {before}, осталось 0 (проверено через Android).")
            return finish(
                f"Частично: очищено {before - after} из {before}; осталось {after} — "
                "это постоянные (служебные) уведомления, кнопкой они не убираются."
            )
        # Nothing changed: the button was a decoy or the tap missed. The loop
        # tries the next candidate; with none left, fall through to the report.

    if not saw_candidates:
        counted = f" (активных уведомлений: {before})" if before is not None else ""
        return finish(
            "Кнопку «Очистить все» в шторке найти не удалось — уведомления НЕ тронуты"
            + counted + ". Шторка закрыта, планшет в прежнем состоянии."
        )
    counted = f"осталось {before}" if before is not None else "число уведомлений не удалось проверить"
    return finish(
        "Кнопка «Очистить все» не уменьшила список: " + counted + " — похоже, это постоянные "
        "служебные уведомления, которые Android не убирает кнопкой."
    )


# --- Карта Android-устройства: JARVIS учится, где что находится --------------
# Знание о экранах и элементах хранится по одному JSON на физическое
# устройство (core/android_map.py). Координаты — только подсказка: нажатие
# всегда ищет элемент заново по подписи/id на свежем дампе (tap_element), а
# результат проверяется по смене экрана и запоминается как переход.

_RISKY_WORDS = (
    "удали", "удаление", "стереть", "сброс", "сбросить", "очистить данные",
    "формат", "factory", "wipe", "erase", "uninstall", "delete", "reset",
    "оплат", "купить", "покупк", "подпис", "перевес", "списать",
    "отправить", "send", "позвонить", "вызов", "call", "pay", "buy", "purchase",
    "выйти из", "выход из", "sign out", "log out", "заблокировать аккаунт",
)


def _amap():
    from core import android_map

    return android_map


def _flag(value: object, default: bool = False) -> bool:
    """JSON-параметры приходят и настоящими bool, и строками "true"/"false"."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "да", "on"}


def _is_risky(label: str) -> bool:
    low = (label or "").lower()
    return any(word in low for word in _RISKY_WORDS)


def _device_identity(serial: str) -> dict:
    """Постоянный ключ устройства + шапка карты.

    Ключ строится из `ro.serialno` (запасной вариант — android_id, затем
    отпечаток сборки), поэтому Wi-Fi- и USB-подключения одного аппарата
    делят одну карту.
    """
    props: dict[str, str] = {}
    try:
        raw = _run(["shell", "getprop"], serial, timeout=15)
        props = dict(re.findall(r"\[([^\]]+)\]:\s*\[([^\]]*)\]", raw))
    except RuntimeError:
        pass
    key_source = props.get("ro.serialno", "")
    if not key_source:
        try:
            key_source = _run(
                ["shell", "settings", "get", "secure", "android_id"], serial, timeout=10
            ).strip()
        except RuntimeError:
            key_source = ""
    if not key_source:
        key_source = (props.get("ro.product.model", "") or "android") + "-" + props.get(
            "ro.build.fingerprint", ""
        )
    screen = ""
    try:
        match = re.search(r"Physical size:\s*(\d+x\d+)", _run(["shell", "wm", "size"], serial, timeout=10))
        if match:
            screen = match.group(1)
    except RuntimeError:
        pass
    market = (
        props.get("ro.product.marketname")
        or props.get("ro.product.odm.marketname")
        or props.get("ro.product.vendor.marketname", "")
    )
    return {
        "key": _amap().safe_key(key_source),
        "name": market or props.get("ro.product.model") or serial,
        "model": props.get("ro.product.model", ""),
        "android": props.get("ro.build.version.release", ""),
        "sdk": props.get("ro.build.version.sdk", ""),
        "screen": screen,
        "locale": props.get("persist.sys.locale") or props.get("ro.product.locale", ""),
        "serial": serial,
    }


def _current_screen(serial: str) -> tuple[str, str]:
    """(package, activity) того, что сейчас в фокусе; («», «») — не удалось."""
    try:
        window = _run(["shell", "dumpsys", "window"], serial, timeout=25)
    except RuntimeError:
        window = ""
    match = re.search(r"mCurrentFocus=Window\{[^}]*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)\}", window)
    if not match:
        try:
            activities = _run(["shell", "dumpsys", "activity", "activities"], serial, timeout=25)
        except RuntimeError:
            activities = ""
        match = re.search(r"mResumedActivity:.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)", activities)
    if not match:
        return "", ""
    app, activity = match.group(1), match.group(2)
    if activity.startswith("."):
        activity = app + activity
    return app, activity


def _collect_elements(root: ET.Element, limit: int = 120) -> list[dict]:
    """Подписанные/нажимаемые узлы дампа — то, что стоит запомнить в карте.

    Порядок отбора: сначала ВСЕ подписанные (text/content-desc) элементы, затем
    нажимаемые без подписи (переключатели и т.п.). Безликие контейнеры
    (icon_frame, icon, обёртки) пропускаются: они съедали бюджет раньше, чем
    обход доходил до правой панели двухпанельных Настроек.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    labeled: list[dict] = []
    unlabeled: list[dict] = []
    seen: set[tuple] = set()
    for node in root.iter("node"):
        label = (node.attrib.get("text") or "").strip() or (node.attrib.get("content-desc") or "").strip()
        rid = (node.attrib.get("resource-id") or "").strip()
        if not (label or rid):
            continue
        clickable = node.attrib.get("clickable") == "true"
        target = node
        if not clickable and label:  # подпись строки часто лежит на дочернем view
            current = node
            for _ in range(3):
                parent = parents.get(current)
                if parent is None:
                    break
                if parent.attrib.get("clickable") == "true":
                    target, clickable = parent, True
                    break
                current = parent
        if not label and not clickable:
            continue  # безликий контейнер — для карты бесполезен
        center = _bounds_center(target.attrib.get("bounds", ""))
        if center is None:
            continue
        dedup_key = (label[:80], rid, center)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        item = {
            "label": label[:80],
            "id": rid,
            "cls": (node.attrib.get("class") or "").rsplit(".", 1)[-1],
            "clickable": clickable,
            "c": list(center),
        }
        (labeled if label else unlabeled).append(item)
    return (labeled + unlabeled)[:limit]


def _find_elements(root: ET.Element, text: str = "", element_id: str = "") -> list[dict]:
    """Нажимаемые цели по подписи/id, лучшая первой.

    Сопоставление идёт на СВЕЖЕМ дампе: точная подпись → начало → вхождение;
    нажимаемое важнее ненажимаемого; при равенстве — выше на экране.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    query_text = (text or "").strip().lower()
    query_id = (element_id or "").strip().lower()
    if not (query_text or query_id):
        return []
    found: list[dict] = []
    for node in root.iter("node"):
        label = ((node.attrib.get("text") or "") + " " + (node.attrib.get("content-desc") or "")).strip()
        node_id = (node.attrib.get("resource-id") or "").strip()
        low = label.lower()
        if query_text and query_text not in low:
            continue
        if query_id and query_id not in node_id.lower():
            continue
        clickable = node.attrib.get("clickable") == "true"
        target = node
        if not clickable:
            current = node
            for _ in range(3):
                parent = parents.get(current)
                if parent is None:
                    break
                if parent.attrib.get("clickable") == "true":
                    target, clickable = parent, True
                    break
                current = parent
        center = _bounds_center(target.attrib.get("bounds", ""))
        if center is None:
            continue
        if not query_text or low.strip() == query_text:
            rank = 0
        elif low.startswith(query_text):
            rank = 1
        else:
            rank = 2
        found.append({"label": label[:80], "id": node_id, "clickable": clickable, "c": center, "rank": rank})
    best: dict[tuple[int, int], dict] = {}
    for item in found:  # дубли по центру: остаётся лучший (точнее и нажимаемый)
        current = best.get(item["c"])
        if current is None or (item["rank"], not item["clickable"]) < (current["rank"], not current["clickable"]):
            best[item["c"]] = item
    return sorted(best.values(), key=lambda it: (it["rank"], not it["clickable"], it["c"][1], it["c"][0]))


def _survey_screen(serial: str, name: str = "") -> str:
    """Записать текущий экран (подписи, id, нажимаемость) в карту устройства."""
    root = _ui_root(serial)
    if root is None:
        return "Не удалось снять интерфейс текущего экрана — в карту ничего не записано."
    app, activity = _current_screen(serial)
    elements = _collect_elements(root)
    if not elements:
        return "На экране нет подписанных элементов — в карту ничего не записано."
    amap = _amap()
    ident = _device_identity(serial)
    data = amap.load_device(ident["key"])
    amap.touch_device(data, ident)
    info = amap.record_screen(data, app=app, activity=activity, elements=elements, name=name.strip())
    amap.save_device(ident["key"], data)
    visible = [el["label"] or el["id"] for el in elements][:18]
    where = name.strip() or activity or app or "текущий экран"
    return (
        f"Экран «{where}» записан в карту устройства {ident['name']}: "
        f"элементов {info['total']} (новых {info['added']}, обновлено {info['updated']}). "
        "Видимые подписи: " + "; ".join(visible) + "."
    )


def _survey_device(serial: str, go_home: bool = True) -> str:
    """Обзор устройства: домашний экран + установленные приложения."""
    if _is_locked(serial) is True:
        return "Планшет заблокирован — обзор невозможен. Разблокируй (action=unlock) и повтори."
    amap = _amap()
    ident = _device_identity(serial)
    data = amap.load_device(ident["key"])
    amap.touch_device(data, ident)
    home_part = ""
    if go_home:
        try:
            _run(["shell", "input", "keyevent", "KEYCODE_HOME"], serial, timeout=10)
            time.sleep(1.4)
        except RuntimeError:
            pass
    root = _ui_root(serial)
    if root is not None:
        app, activity = _current_screen(serial)
        elements = _collect_elements(root)
        if elements:
            info = amap.record_screen(
                data, app=app, activity=activity, elements=elements,
                name="Домашний экран" if go_home else "",
            )
            labels = [el["label"] or el["id"] for el in elements][:15]
            home_part = f"Главный экран записан ({info['total']} элементов): " + "; ".join(labels) + ". "
    installed: list[str] = []
    try:
        packages = _run(["shell", "pm", "list", "packages", "-3"], serial, timeout=30)
        installed = [
            ln.split("package:", 1)[1].strip()
            for ln in packages.splitlines()
            if ln.startswith("package:")
        ]
    except (RuntimeError, IndexError):
        installed = []
    for package in installed:
        amap.record_app(data, package)
    amap.save_device(ident["key"], data)
    if installed:
        shorts = ", ".join(sorted({pkg.rsplit(".", 1)[-1] for pkg in installed})[:12])
        apps_part = f"Сторонних приложений: {len(installed)} (например: {shorts}) — все записаны в каталог."
    else:
        apps_part = "Список приложений получить не удалось."
    return (
        f"Обзор устройства {ident['name']} (Android {ident['android']}, экран {ident['screen']}). "
        + home_part + apps_part
        + " Теперь изучай экраны через survey_screen и нажимай через tap_element."
    )


def _tap_element(
    serial: str,
    text: str = "",
    element_id: str = "",
    index: object = 0,
    allow_risky: bool = False,
) -> str:
    """Нажать по подписи/id: поиск на свежем экране, проверка результата, запись в карту."""
    if not (text.strip() or element_id.strip()):
        return "Укажи text (подпись элемента) или element_id (resource-id) — по чему нажимать."
    app_before, activity_before = _current_screen(serial)
    root = _ui_root(serial)
    if root is None:
        return "Не удалось снять интерфейс текущего экрана — нажатие отменено."
    matches = _find_elements(root, text=text, element_id=element_id)
    if not matches:
        clickable_labels: list[str] = []
        for node in root.iter("node"):
            label = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
            if label and node.attrib.get("clickable") == "true":
                clickable_labels.append(label[:40])
            if len(clickable_labels) >= 15:
                break
        listed = "; ".join(clickable_labels) if clickable_labels else "(нет)"
        return (
            f"Элемент «{text or element_id}» на экране не найден — нажатие НЕ выполнено. "
            "Нажимаемые подписи сейчас: " + listed + "."
        )
    try:
        pick_index = max(0, min(int(index or 0), len(matches) - 1))
    except (TypeError, ValueError):
        pick_index = 0
    pick = matches[pick_index]
    label = pick["label"] or pick["id"]
    if _is_risky(label) and not allow_risky:
        return (
            f"Не нажимаю «{label}»: это выглядит как необратимое или «отправляющее» действие. "
            "Если владелец прямо просит — повтори с allow_risky=true."
        )
    x, y = pick["c"]
    signature_before = _screen_signature(root)
    _run(["shell", "input", "tap", str(x), str(y)], serial, timeout=10)
    time.sleep(1.2)
    app_after, activity_after = _current_screen(serial)
    activity_changed = bool(app_after or activity_after) and (app_after, activity_after) != (app_before, activity_before)
    after_root = _ui_root(serial)
    view_changed = False
    if after_root is not None:
        view_changed = _signature_overlap(signature_before, _screen_signature(after_root)) < 0.8
    changed = activity_changed or view_changed

    amap = _amap()
    ident = _device_identity(serial)
    data = amap.load_device(ident["key"])
    amap.touch_device(data, ident)
    alternatives = len(matches) - 1
    alt_note = f" Совпадений было {len(matches)} — выбрал №{pick_index + 1}." if alternatives else ""
    if not changed:
        amap.save_device(ident["key"], data)
        return (
            f"Нажал «{label}» ({x},{y}), но экран не изменился — возможно, это переключатель, "
            "а не переход, или нажатие ничего не сделало." + alt_note
            + " Проверь свежим inspect_screen."
        )
    screen_app = app_after or app_before
    screen_activity = activity_after or activity_before
    from_key = amap.screen_key(app_before, activity_before)
    to_key = amap.screen_key(screen_app, screen_activity)
    amap.record_exit(data, from_key, label, to_key)
    learned = ""
    if after_root is not None:
        elements = _collect_elements(after_root)
        if elements:
            info = amap.record_screen(data, app=screen_app, activity=screen_activity, elements=elements)
            learned = f" Вид записан в карту: {info['screen_key']} ({info['total']} элементов, новых {info['added']})."
    amap.save_device(ident["key"], data)
    if activity_changed:
        route = (
            f" Экран сменился: {activity_before or app_before or '?'} → {activity_after or app_after}."
            " Переход запомнен."
        )
    else:
        route = " Экран тот же, но интерфейс изменился (встроенная панель — не переход между экранами)."
    return f"Нажал «{label}» ({x},{y})." + alt_note + route + learned


def _recall_place(serial: str, query: str) -> str:
    """Что JARVIS уже знает об устройстве по этому запросу («где что»)."""
    amap = _amap()
    ident = _device_identity(serial)
    data = amap.load_device(ident["key"])
    query = (query or "").strip()
    if not query:
        return amap.overview(data)
    hits = amap.search(data, query, limit=8)
    if not hits:
        return (
            f"В карте устройства {ident['name']} по запросу «{query}» пусто. "
            "Изучи сам: open_app → survey_screen → tap_element — карта заполнится, и путь запомнится."
        )
    lines = [f"Карта устройства {ident['name']} — что известно по «{query}»:"]
    for hit in hits:
        kind = hit.get("kind")
        if kind == "function":
            steps = " → ".join(hit.get("steps") or []) or "шаги не записаны"
            note = f" (заметка: {hit['notes']})" if hit.get("notes") else ""
            lines.append(f"• [знание] «{hit.get('goal')}»: {steps}{note} — обновлено {hit.get('updated', '')}")
        elif kind == "route":
            lines.append(
                f"• [переход] на экране «{hit.get('screen')}» нажатие «{hit.get('label')}» "
                f"открывает {hit.get('dest')}"
            )
        elif kind == "element":
            state = "нажимаемый" if hit.get("clickable") else "не нажимаемый"
            lines.append(
                f"• [элемент] «{hit.get('label')}» на экране «{hit.get('screen')}» ({state}), "
                f"видел {hit.get('seen', '?')}"
            )
        elif kind == "app":
            lines.append(f"• [приложение] {hit.get('label')} — {hit.get('package')}")
    lines.append(
        "Координаты в карте — только подсказка: нажимай через tap_element (text=…), он найдёт элемент заново."
    )
    return "\n".join(lines)


def _note_place(
    serial: str, goal: str, app: str = "", screen: str = "", steps: str = "", notes: str = ""
) -> str:
    """Сохранить в карту найденный путь: что сделать и куда нажимать."""
    goal = (goal or "").strip()
    if not goal:
        return "Укажи goal — что за функция (например, «включить Wi-Fi»)."
    amap = _amap()
    ident = _device_identity(serial)
    data = amap.load_device(ident["key"])
    amap.touch_device(data, ident)
    key = amap.note_function(
        data, goal=goal, app=app, screen=screen, steps=steps, notes=notes, source="jarvis"
    )
    amap.save_device(ident["key"], data)
    saved = (data.get("functions") or {}).get(key, {})
    return (
        f"Запомнил в карте устройства {ident['name']}: «{saved.get('goal', goal)}» — "
        f"шагов: {len(saved.get('steps') or [])}."
    )


def _map_overview(serial: str) -> str:
    """Что JARVIS уже знает об этом устройстве (экраны, приложения, пути)."""
    amap = _amap()
    ident = _device_identity(serial)
    data = amap.load_device(ident["key"])
    return amap.overview(data)


def android_control(parameters=None, player=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "status")).strip().lower()
    if action == "list_devices":
        return _adb_all_devices()
    if action == "wifi_connect":
        return _wifi_connect(str(p.get("endpoint", "")))
    if action == "wifi_disconnect":
        endpoint = str(p.get("endpoint", "")).strip()
        if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}", endpoint):
            raise RuntimeError("Укажите IPv4:порт Wi-Fi ADB, который нужно отключить.")
        return _run(["disconnect", endpoint], timeout=10)
    if action == "wifi_pair":
        return _wifi_pair(str(p.get("endpoint", "")), str(p.get("code", "")))
    if action == "set_unlock_password":
        return _set_unlock_password(str(p.get("password", "")), str(p.get("device_id", "")).strip())
    serial = _select_device(str(p.get("device_id", "")).strip())

    if action in {"unlock", "lock", "lock_state"}:
        if action == "lock_state":
            locked = _is_locked(serial)
            if locked is None:
                return "Не удалось определить состояние блокировки планшета."
            return "Экран планшета заблокирован." if locked else "Экран разблокирован — можно управлять планшетом."
        if action == "lock":
            return _lock_tablet(serial)
        password = str(p.get("password", "")).strip()
        if not password:
            from memory.config_manager import get_tablet_unlock_password

            password = get_tablet_unlock_password(serial)
        return _unlock_sequence(serial, password)

    if action == "clear_notifications":
        return _clear_notifications(serial)

    if action == "survey_screen":
        return _survey_screen(serial, str(p.get("name", "")).strip())

    if action == "survey_device":
        return _survey_device(serial, _flag(p.get("home"), default=True))

    if action == "tap_element":
        return _tap_element(
            serial,
            text=str(p.get("text", "")),
            element_id=str(p.get("element_id", "")),
            index=p.get("index"),
            allow_risky=_flag(p.get("allow_risky")),
        )

    if action == "recall_place":
        return _recall_place(serial, str(p.get("query", "")))

    if action == "note_place":
        return _note_place(
            serial,
            goal=str(p.get("goal", "")),
            app=str(p.get("app", "")),
            screen=str(p.get("screen", "")),
            steps=str(p.get("steps", "")),
            notes=str(p.get("notes", "")),
        )

    if action == "map_overview":
        return _map_overview(serial)

    if action == "install_apk":
        raw_path = str(p.get("apk_path", "")).strip()
        if not raw_path:
            raise RuntimeError("Укажите полный путь к APK-файлу на компьютере.")
        apk = Path(raw_path).expanduser().resolve()
        if apk.suffix.lower() != ".apk" or not apk.is_file():
            raise RuntimeError("Указанный путь должен вести к существующему файлу .apk на компьютере.")
        size = apk.stat().st_size
        if not 1 <= size <= 2 * 1024 * 1024 * 1024:
            raise RuntimeError("Размер APK должен быть от 1 байта до 2 ГБ.")
        result = _run(["install", "-r", str(apk)], serial, timeout=180)
        if "success" not in result.lower():
            raise RuntimeError(f"ADB не подтвердил установку APK: {result[:500]}")
        return f"APK установлен на устройство {serial}: {apk.name}. Ответ ADB: {result}"

    if action == "observe_screen":
        frame = _tablet_frame(str(p.get("device_id", "")).strip())
        if not frame:
            raise RuntimeError("Свежий кадр планшета не получен. Проверьте ADB-подключение.")
        return "Получен свежий кадр экрана Android.", frame

    if action == "status":
        model = _run(["shell", "getprop", "ro.product.model"], serial) or "Android"
        version = _run(["shell", "getprop", "ro.build.version.release"], serial)
        size = _run(["shell", "wm", "size"], serial)
        return f"Подключено: {model}; Android {version}; {size}; device_id={serial}."

    if action in {"screen_on", "screen_off", "volume_up", "volume_down", "volume_mute"}:
        key = {
            "screen_on": "KEYCODE_WAKEUP",
            "screen_off": "KEYCODE_SLEEP",
            "volume_up": "KEYCODE_VOLUME_UP",
            "volume_down": "KEYCODE_VOLUME_DOWN",
            "volume_mute": "KEYCODE_VOLUME_MUTE",
        }[action]
        _run(["shell", "input", "keyevent", key], serial)
        if action == "screen_on":
            return "Экран планшета разбужен. Если была пауза в задаче, проверьте Android через inspect_screen и продолжите её; после вашей разблокировки можно управлять приложениями."
        labels = {
            "screen_on": "Экран включён",
            "screen_off": "Экран выключен",
            "volume_up": "Громкость увеличена на один шаг",
            "volume_down": "Громкость уменьшена на один шаг",
            "volume_mute": "Переключено отключение звука",
        }
        return labels[action] + "."

    if action == "get_brightness":
        return _brightness(serial)

    if action == "set_brightness":
        return _set_brightness(serial, p.get("value"))

    if action == "auto_brightness":
        _run(["shell", "settings", "put", "system", "screen_brightness_mode", "1"], serial)
        return "Автояркость включена."

    if action == "open_app":
        package = str(p.get("package", "")).strip()
        if not _PACKAGE_RE.fullmatch(package):
            raise RuntimeError("Укажите package приложения, например com.android.settings.")
        # Launch by explicit component (resolved launcher activity): this works
        # even on ROMs where `monkey` is absent or aborts with a usage dump, and
        # it names what was actually started. Only if resolution fails do we
        # check that the package exists and then fall back to monkey.
        activity = ""
        try:
            resolved = _run(
                [
                    "shell", "cmd", "package", "resolve-activity", "--brief",
                    "-c", "android.intent.category.LAUNCHER", package,
                ],
                serial,
                timeout=30,
            )
            activity = _launcher_activity(resolved)
        except RuntimeError:
            activity = ""
        if not activity:
            installed = _installed_packages(serial)
            if installed and package not in installed:
                frag = package.rsplit(".", 1)[-1].lower()
                near = [
                    pkg for pkg in sorted(installed) if frag and frag in pkg.lower()
                ][:5]
                hint = (" Похожие: " + ", ".join(near) + ".") if near else ""
                raise RuntimeError(
                    f"Приложение {package} не установлено на устройстве.{hint}"
                )
            _run(
                [
                    "shell", "monkey", "-p", package,
                    "-c", "android.intent.category.LAUNCHER", "1",
                ],
                serial,
            )
            return f"Открыто приложение {package}."
        _run(["shell", "am", "start", "-n", activity], serial, timeout=30)
        return f"Открыто приложение {package} ({activity})."

    if action == "tap":
        x, y = int(p.get("x", -1)), int(p.get("y", -1))
        if not (0 <= x <= 10000 and 0 <= y <= 10000):
            raise RuntimeError("Координаты касания должны быть от 0 до 10000.")
        _run(["shell", "input", "tap", str(x), str(y)], serial)
        return f"Касание на Android: ({x}, {y})."

    if action == "swipe":
        coords = [int(p.get(key, -1)) for key in ("x1", "y1", "x2", "y2")]
        if any(value < 0 or value > 10000 for value in coords):
            raise RuntimeError("Координаты свайпа должны быть от 0 до 10000.")
        duration = int(p.get("duration_ms", 350))
        if not 50 <= duration <= 5000:
            raise RuntimeError("Длительность свайпа должна быть от 50 до 5000 мс.")
        _run(["shell", "input", "swipe", *map(str, coords), str(duration)], serial)
        return "Свайп выполнен."

    if action == "type_text":
        text = str(p.get("text", ""))
        if not text or len(text) > _MAX_TEXT:
            raise RuntimeError(f"Текст должен содержать от 1 до {_MAX_TEXT} символов.")
        # `input text` has a small shell-like escape language. Restrict it to
        # printable ASCII; arbitrary Unicode needs a separately installed IME.
        if any(ord(char) < 32 or ord(char) > 126 for char in text):
            raise RuntimeError("ADB вводит только печатный ASCII. Для русского текста установите ADB Keyboard на телефоне.")
        if re.search(r"[^A-Za-z0-9 .,!?@#%+=:_/-]", text):
            raise RuntimeError("Для безопасности разрешены только латинские буквы, цифры, пробел и простая пунктуация.")
        escaped = text.replace("%", "%25").replace(" ", "%s")
        _run(["shell", "input", "text", escaped], serial)
        return f"Текст введён ({len(text)} символов)."

    if action == "press_key":
        key = str(p.get("key", "")).strip().lower()
        if key not in _KEYS:
            raise RuntimeError(f"Недопустимая кнопка. Доступны: {', '.join(_KEYS)}.")
        _run(["shell", "input", "keyevent", _KEYS[key]], serial)
        return f"Нажата кнопка {_KEYS[key]} на Android."

    if action == "screenshot":
        out_dir = Path.home() / "Pictures" / "JARVIS"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = (out_dir / "android_screen.png").resolve()
        # Stream bytes directly; never interpolate a path into a remote shell.
        adb = _adb_path()
        result = subprocess.run(
            [adb, "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Не удалось получить скриншот Android.")
        path.write_bytes(result.stdout)
        return f"Скриншот сохранён: {path}"

    if action == "inspect_screen":
        root = _ui_root(serial)
        if root is None:
            raise RuntimeError("Android вернул некорректное дерево интерфейса.")
        nodes = []
        for node in root.iter("node"):
            text = (node.attrib.get("text") or "").strip()
            description = (node.attrib.get("content-desc") or "").strip()
            resource_id = (node.attrib.get("resource-id") or "").strip()
            bounds = node.attrib.get("bounds", "")
            clickable = node.attrib.get("clickable") == "true"
            if not (text or description or resource_id or clickable):
                continue
            label = text or description or resource_id or "(без подписи)"
            nodes.append(f"{'[нажимаемый] ' if clickable else ''}{label[:100]} {bounds}".strip())
            if len(nodes) >= 100:
                break
        return "Элементы экрана (используй bounds для центра касания):\n" + ("\n".join(nodes) if nodes else "Не удалось найти подписанные элементы.")

    raise RuntimeError("Действие должно быть одним из: set_unlock_password, status, unlock, lock, lock_state, clear_notifications, survey_screen, survey_device, tap_element, recall_place, note_place, map_overview, open_app, inspect_screen, tap, swipe, type_text, press_key, screen_on, screen_off, volume_up, volume_down, volume_mute, get_brightness, set_brightness, auto_brightness, screenshot, install_apk.")


TOOL = {
    "name": "android_control",
    "description": (
        "Управляет Android-телефоном, который владелец подключил и авторизовал через ADB. "
        "Сначала используй action=status, чтобы узнать подключённое устройство и размер экрана; "
        "если подключено несколько устройств, передавай device_id из serial. "
        "list_devices показывает и уже подключённые устройства, и Android-устройства, видимые в Wi-Fi "
        "(беспроводная отладка). Чтобы подключить новое Android-устройство: владелец включает на нём "
        "«Беспроводную отладку», читает с экрана pairing endpoint (IPv4:порт) и 6-значный код и называет их "
        "JARVIS — JARVIS выполняет wifi_pair (endpoint + code). Код сопряжения не секрет, но повторять "
        "его в ответах не нужно. Также доступны wifi_connect (только локальный IPv4:порт Wireless debugging) и wifi_disconnect. "
        "Разблокировка (только устройство самого владельца, ADB-авторизованное): если планшет заблокирован, "
        "используй unlock — JARVIS вводит сохранённый PIN/пароль (tablet_unlock_password в config) вместо ручного ввода. "
        "Пароль сохраняется один раз через set_unlock_password или в config и никогда не произносится и не повторяется в ответах. "
        "Если пароль не сохранён — unlock откажется и попросит его сохранить; lock выключает и блокирует экран, "
        "lock_state сообщает, заблокирован ли планшет. Никогда не обходи логины приложений, коды из SMS/email, "
        "подписки и согласия — это остаётся за владельцем. "
        "Доступны screen_on/screen_off, volume_up/volume_down/volume_mute, get_brightness, "
        "set_brightness (value 0-100, включает ручной режим) и auto_brightness; "
        "open_app (нужен package приложения), inspect_screen (подписи и bounds элементов), "
        "tap и swipe (координаты экрана), "
        "type_text (печатный ASCII; русский текст требует ADB Keyboard) и press_key "
        "(только разрешённый список). screenshot сохраняет файл на компьютере; для управления интерфейсом используй inspect_screen. "
        "install_apk устанавливает локальный APK на выбранный авторизованный планшет только по прямой просьбе пользователя; укажи полный путь apk_path. Это не скачивает APK и не обходит системные подтверждения Android. "
        "Используй UI-действия как общий процесс, а не фиксированный список задач: для обычной цели проверь текущий экран, выбери подписанный элемент и нажми по его bounds, затем снова проверь экран; при необходимости используй свайп, ввод и кнопки, проверяя результат. Если package приложения неизвестен, найди приложение на видимом рабочем столе, не выдумывай package. Не заявляй об успехе без подтверждения, не запускай произвольные shell-команды и не обходи системные запросы разрешений. При просьбе разбудить планшет используй screen_on; если планшет заблокирован, сначала unlock (пароль уже сохранён), затем продолжай задачу; если пользователь говорит «теперь», «давай», «продолжай» — вспомни исходную задачу и продолжи workflow без повторного вопроса. Перед продолжением после паузы проверь текущий Android экран через inspect_screen. Не утверждай, что видишь экран, если не запросил свежий inspect_screen или screenshot именно планшета. "
        "Никогда не утверждай, что действие выполнено, пока инструмент не вернул успех. "
        "Для просьб «очисти уведомления», «убери уведомления с планшета» используй только clear_notifications: "
        "он сам открывает шторку, нажимает «Очистить все» и проверяет результат счётчиком Android. "
        "В ответе инструмента точные числа «было/осталось» — перескажи именно их; при отказе или частичном "
        "результате скажи честно, НЕ утверждай, что уведомления очищены. "
        "Карта Android-устройства (самообучение, где что находится): survey_screen запоминает текущий экран "
        "(подписи, resource-id, нажимаемость), survey_device даёт общий обзор (домашний экран + установленные "
        "приложения). tap_element нажимает по подписи или id — элемент всегда ищется заново на свежем экране, "
        "поэтому старые координаты не решают, куда попадёт нажатие — и автоматически запоминает переходы и "
        "новые экраны. recall_place отвечает на «где что» по накопленной карте, note_place сохраняет путь "
        "вручную, map_overview показывает, что уже изучено. Порядок для вопроса «где X»: сначала recall_place; "
        "если знания нет или оно устарело — открой приложение (open_app), survey_screen, дойди через tap_element "
        "(карта пополнится сама), затем расскажи путь. Карта — отдельная для каждого устройства и переживает "
        "перезапуски JARVIS. При изучении не нажимай необратимые и «отправляющие» кнопки (удалить, оплатить, "
        "отправить, выйти) без прямой просьбы владельца — для них нужен allow_risky=true."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list_devices (подключённые и видимые в Wi-Fi Android-устройства), wifi_pair (сопряжение с новым устройством: передай endpoint и code с экрана Wireless debugging), wifi_connect, wifi_disconnect, set_unlock_password (сохранить PIN/пароль устройства; device_id — необязательно), status, lock_state (заблокирован ли планшет), unlock (ввести сохранённый пароль и разблокировать), lock (выключить и заблокировать экран), clear_notifications (очистить все уведомления в шторке с проверкой результата), survey_screen (запомнить текущий экран в карту устройства; name — имя экрана), survey_device (обзор устройства: домашний экран + установленные приложения), tap_element (нажать элемент по подписи/id с проверкой и записью в карту), recall_place (вспомнить «где что» по карте устройства; query), note_place (сохранить путь вручную: goal, steps), map_overview (что уже изучено об устройстве), observe_screen (получить свежий кадр активного планшета для визуального осмотра), open_app, inspect_screen (подписи и bounds Android), tap, swipe, type_text, press_key, screen_on, screen_off, volume_up, volume_down, volume_mute, get_brightness, set_brightness, auto_brightness, screenshot или install_apk (установка локального APK по прямой просьбе пользователя)"},
            "apk_path": {"type": "STRING", "description": "Полный локальный путь к APK на ПК; только для install_apk"},
            "endpoint": {"type": "STRING", "description": "Локальный IPv4:порт из меню Android Wireless debugging для wifi_connect/wifi_disconnect/wifi_pair"},
            "code": {"type": "STRING", "description": "6-значный код сопряжения с экрана Android Wireless debugging; только для wifi_pair"},
            "device_id": {"type": "STRING", "description": "Устройство: ADB serial (ip:port или adb-…) либо понятное имя (Redmi, RT2, OUKITEL). Укажи, если подключено больше одного Android-устройства — по имени можно выбрать устройство из голосовой команды («на редми», «на планшете»)."},
            "package": {"type": "STRING", "description": "Package приложения для open_app, например com.android.settings. Собственное приложение JARVIS на планшете/телефоне — com.jarvis.remote. Если package неизвестен, не выдумывай его: сначала посмотри рабочий стол через inspect_screen."},
            "x": {"type": "INTEGER", "description": "X для tap"},
            "y": {"type": "INTEGER", "description": "Y для tap"},
            "x1": {"type": "INTEGER", "description": "Начало свайпа X"},
            "y1": {"type": "INTEGER", "description": "Начало свайпа Y"},
            "x2": {"type": "INTEGER", "description": "Конец свайпа X"},
            "y2": {"type": "INTEGER", "description": "Конец свайпа Y"},
            "duration_ms": {"type": "INTEGER", "description": "Длительность свайпа 50–5000 мс"},
            "text": {"type": "STRING", "description": "type_text: текст для ввода (печатный ASCII). tap_element: подпись элемента для поиска на текущем экране (например, Wi-Fi)."},
            "key": {"type": "STRING", "description": "back, home, recent, volume_up, volume_down, power, enter, delete, space или tab"},
            "value": {"type": "INTEGER", "description": "Яркость экрана в процентах 0-100 для set_brightness"},
            "password": {"type": "STRING", "description": "PIN/пароль планшета для set_unlock_password (сохранение) или unlock (если не сохранён в конфиге). Никогда не повторяй его в ответе."},
            "element_id": {"type": "STRING", "description": "tap_element: часть resource-id элемента вместо подписи (если у элемента нет текста)"},
            "index": {"type": "INTEGER", "description": "tap_element: какой из найденных совпадающих элементов нажать (0 — лучший, по умолчанию)"},
            "allow_risky": {"type": "BOOLEAN", "description": "tap_element: разрешить нажатие необратимых кнопок (удалить/оплатить/отправить/выйти). Только по прямой просьбе владельца"},
            "name": {"type": "STRING", "description": "survey_screen: имя текущего экрана для карты (например, Настройки)"},
            "query": {"type": "STRING", "description": "recall_place: что искать в карте устройства («wifi», «уведомления», «будильник»). Пиши ключевые слова как в интерфейсе; пусто — показать всю карту"},
            "goal": {"type": "STRING", "description": "note_place: для чего путь (например, «включить Wi-Fi»)"},
            "app": {"type": "STRING", "description": "note_place: package приложения, к которому относится знание"},
            "steps": {"type": "STRING", "description": "note_place: шаги пути через ; или → (например, «Настройки; Сеть и интернет; Wi-Fi»)"},
            "notes": {"type": "STRING", "description": "note_place: заметка к знанию (как выглядит, на что смотреть)"},
            "home": {"type": "BOOLEAN", "description": "survey_device: вернуться на домашний экран для обзора (по умолчанию true)"},
        },
        "required": ["action"],
    },
    "handler": android_control,
}
