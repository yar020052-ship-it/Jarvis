"""Read-only discovery of devices on the computer's active private LAN.

Sources merged into one deduplicated list:
  * an ICMP + common-TCP-port sweep of the subnet,
  * the local ARP table (catches devices that ignore ping but the PC has
    recently talked to),
  * mDNS services (Android devices with wireless debugging announce themselves
    as ``_adb-tls-connect``, so JARVIS sees them even before any pairing).

Read-only: never logs in, sends device commands, exploits services, changes
settings, or controls discovered devices. Controlling an Android device found
here is done separately through the ``android_control`` tool after the owner
enables wireless debugging and pairs it.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import psutil

from actions.android_control import _adb_path

_PORTS = {
    21: "FTP", 22: "SSH", 23: "Telnet", 53: "DNS", 80: "HTTP",
    139: "NetBIOS", 443: "HTTPS", 445: "SMB", 554: "RTSP",
    5555: "Android ADB", 631: "IPP принтер", 8000: "HTTP 8000",
    8080: "HTTP 8080", 8443: "HTTPS 8443", 9100: "печать JetDirect",
    3389: "RDP", 5900: "VNC", 32400: "Plex",
}
_RFC1918 = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))


def _active_private_network() -> tuple[str, ipaddress.IPv4Network]:
    # A UDP connect selects the route and local interface without sending a packet.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        local_ip = probe.getsockname()[0]
    except OSError as exc:
        raise RuntimeError("Не удалось определить активный сетевой интерфейс компьютера.") from exc
    finally:
        probe.close()

    for name, addresses in psutil.net_if_addrs().items():
        for item in addresses:
            if item.family != socket.AF_INET or item.address != local_ip or not item.netmask:
                continue
            interface = ipaddress.ip_interface(f"{item.address}/{item.netmask}")
            network = interface.network
            if not any(interface.ip in private for private in _RFC1918):
                raise RuntimeError("Активный маршрут не относится к частной домашней сети; сканирование остановлено.")
            if network.num_addresses > 256:
                raise RuntimeError(
                    f"Подсеть {network} слишком велика для безопасного автоматического поиска "
                    "(лимит — 256 адресов). Измените маску домашней сети или сузьте диапазон."
                )
            if network.num_addresses < 4:
                raise RuntimeError("В локальной подсети недостаточно адресов для поиска устройств.")
            return name, network
    raise RuntimeError("Не удалось сопоставить активный IP-адрес с интерфейсом компьютера.")


def _ping(ip: str) -> bool:
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", "220", ip],
            capture_output=True, timeout=1.2, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _probe_port(ip: str, port: int) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=0.16):
            return True
    except OSError:
        return False


def _scan_host(ip: str) -> tuple[str, bool, list[int]]:
    ping_ok = _ping(ip)
    opened = [port for port in _PORTS if _probe_port(ip, port)]
    return ip, ping_ok or bool(opened), opened


def _reverse_name(ip: str) -> str:
    try:
        name = socket.gethostbyaddr(ip)[0].rstrip(".")
    except (OSError, socket.herror):
        return ""
    if name == ip or not re.fullmatch(r"[A-Za-z0-9_.-]{1,253}", name):
        return ""
    return name


def _arp_macs() -> dict[str, str]:
    """Parse `arp -a` into {ip: mac} for real RFC1918 hosts (no broadcast/multicast)."""
    macs: dict[str, str] = {}
    try:
        result = subprocess.run(
            ["arp", "-a"],
            capture_output=True, text=True, timeout=15, check=False,
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return macs
    pattern = re.compile(
        r"\b((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})\s+"
        r"([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})\b"
    )
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        ip, mac = match.group(1), match.group(2)
        if not ipaddress.ip_address(ip).is_private:
            continue
        mac = mac.lower().replace("-", ":")
        if mac == "ff:ff:ff:ff:ff:ff" or mac.startswith("01:00:5e:"):
            continue
        macs[ip] = mac
    return macs


def _adb_mdns_hosts() -> dict[str, dict]:
    """Android wireless-debugging devices announced via mDNS: {ip: {name, endpoint}}."""
    hosts: dict[str, dict] = {}
    try:
        result = subprocess.run(
            [_adb_path(), "mdns", "services"],
            capture_output=True, text=True, timeout=10, check=False,
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return hosts
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 5:
            name, srv_type, endpoint = parts[1], parts[2], parts[-1]
        elif len(parts) == 3:
            name, srv_type, endpoint = parts
        else:
            continue
        if srv_type not in {"_adb-tls-connect._tcp", "_adb._tcp"}:
            continue
        match = re.fullmatch(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})", endpoint)
        if not match:
            continue
        hosts[match.group(1)] = {"name": name, "endpoint": endpoint}
    return hosts


def _to_host_lines(network: ipaddress.IPv4Network) -> list[str]:
    arp = _arp_macs()
    mdns = _adb_mdns_hosts()

    # Sweep the subnet; ARP/mDNS-known hosts are included even if they ignore ping.
    swept: dict[str, list[int]] = {}
    sweep_hosts = sorted(
        {str(ip) for ip in network.hosts()} | set(arp) | set(mdns),
        key=lambda ip: ipaddress.ip_address(ip),
    )
    with ThreadPoolExecutor(max_workers=96) as pool:
        futures = [pool.submit(_scan_host, ip) for ip in sweep_hosts]
        for future in as_completed(futures):
            ip, alive, opened = future.result()
            if alive:
                swept[ip] = opened

    alive_hosts = set(swept) | set(arp) | set(mdns)
    if not alive_hosts:
        return [
            "Устройства не ответили. Некоторые роутеры скрывают клиентов или блокируют такие проверки.",
            "Попробуйте: включите беспроводную отладку на Android-устройстве — оно само объявит себя в mDNS.",
        ]

    devices = sorted(alive_hosts, key=ipaddress.ip_address)
    name_pool = ThreadPoolExecutor(max_workers=16)
    name_futures = {name_pool.submit(_reverse_name, ip): ip for ip in devices}
    done, pending = concurrent.futures.wait(name_futures, timeout=1.5)
    ptr_names: dict[str, str] = {}
    for future in done:
        try:
            ptr_names[name_futures[future]] = future.result()
        except Exception:
            pass
    for future in pending:
        future.cancel()
    name_pool.shutdown(wait=False, cancel_futures=True)

    lines = [f"Найдено устройств в сети: {len(devices)}"]
    for ip in devices:
        name = ptr_names.get(ip) or mdns.get(ip, {}).get("name", "")
        label = f"{ip}" + (f" — {name}" if name else "")
        extras = []
        if ip in arp:
            extras.append(f"MAC {arp[ip]}")
        if ip in mdns:
            extras.append("Android (беспроводная отладка) — управление через android_control")
        services = [f"{port}/{_PORTS[port]}" for port in sorted(swept.get(ip, []))]
        if services:
            extras.append("службы: " + ", ".join(services))
        detail = (" · " + "; ".join(extras)) if extras else ""
        lines.append(f"• {label}{detail}")
    return lines


def scan_network() -> str:
    interface, network = _active_private_network()
    lines = [
        f"Поиск в активной локальной подсети {network} (интерфейс {interface}).",
        "Проверены ответы устройств, ARP-таблица, mDNS и распространённые сетевые порты; вход в устройства не выполнялся.",
    ]
    lines += _to_host_lines(network)
    lines.append(
        "Устройства, которые спят или блокируют обнаружение, могут отсутствовать. "
        "Android-устройства с беспроводной отладкой JARVIS видит и без сопряжения; "
        "для управления новым устройством назовите JARVIS endpoint и код сопряжения с экрана Wireless debugging."
    )
    return "\n".join(lines)


TOOL = {
    "name": "network_scan",
    "description": (
        "Find devices on the computer's active RFC1918 private LAN only: ping/ARP/mDNS sweep plus "
        "common open TCP services. Android devices with wireless debugging announce themselves via "
        "mDNS and are listed as controllable. Read-only discovery: one ICMP echo and limited TCP "
        "probes per host, plus `arp -a` and `adb mdns services`; never logs in, sends device "
        "commands, exploits services, changes settings, or controls discovered devices. "
        "Controlling an Android device is handled by the android_control tool after the owner pairs it. "
        "Use only when the user asks to discover devices on their home/local network."
    ),
    "parameters": {"type": "OBJECT", "properties": {}},
    "handler": lambda **_kwargs: scan_network(),
}