"""
dashboard/server.py — JARVIS Local Dashboard

HTTP or HTTPS on port 8000, depending on the local certificate pair.
Security at the application layer: AES-256-CBC with session-key-derived key.
CryptoJS is auto-downloaded once and served locally — no CDN needed after that.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
import base64
import os
import threading
import re
import secrets
import socket
import string
import time
from pathlib import Path
from core.paths import load_config

_DEPS_OK = False
_DEPS_ERROR = ""
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    import uvicorn

    _DEPS_OK = True
except ImportError as _e:
    _DEPS_ERROR = f"{type(_e).__name__}: {_e}"

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile

    _UPLOAD_OK = True
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).parent / "static"
_DEVICES_FILE = BASE_DIR / "config" / "device_sessions.json"
PORT = 8000
MAX_UPLOAD_MB = 500


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "JARVIS Uploads",
        Path.home() / "Documents" / "JARVIS Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            pass
    return BASE_DIR / "uploads"


UPLOADS_DIR = _make_uploads_dir()


def _get_gemini_key() -> str | None:
    try:
        return load_config("api_keys.json").get("gemini_api_key")
    except Exception:
        return None


_KEY_CHARS = [
    c
    for c in (string.ascii_uppercase + string.digits)
    if c not in ("O", "I", "L", "0", "1")
]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
# Соль и число итераций должны совпадать с app.html (encrypt → CryptoJS.PBKDF2).
# Раньше здесь был один SHA-256: ключ из 6-символьного PIN считался за
# микросекунды, а трафик шёл по plaintext HTTP — перехватанный пакет позволял
# перебрать PIN офлайн. PBKDF2 с большим числом итераций делает такой перебор
# дорогим. Результат кэшируется (_aes_cache), так что цена — один раз за сессию.
_AES_SALT = b"JARVIS-DASHBOARD-v1"
_AES_ITERS = 200_000


def _derive_key(session_key: str) -> bytes:
    """PBKDF2-HMAC-SHA256(sessionKey, salt, 200k) → 32-byte AES-256 key."""
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_AES_SALT,
        iterations=_AES_ITERS,
    )
    return kdf.derive(session_key.encode("utf-8"))


def _decrypt_cbc(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(IV[16] ‖ ciphertext) with AES-256-CBC + PKCS7."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad

    raw = base64.b64decode(enc_b64)
    iv, ct = raw[:16], raw[16:]
    dec = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


# ── CryptoJS (auto-download once, served locally) ─────────────────────────────
_CRYPTOJS_CDN = (
    "https://cdnjs.cloudflare.com/ajax/libs/" "crypto-js/4.2.0/crypto-js.min.js"
)
_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Cross-platform, best-effort: open port in the OS firewall for LAN access.

    Runs in a background thread — never blocks uvicorn startup.

    Windows : writes a .bat file, runs it elevated via Windows ShellExecuteW
              (native UAC dialog, guaranteed to appear). One-time setup.
    macOS   : osascript admin dialog if the Application Firewall is on.
    Linux   : pkexec GUI → sudo -n → prints manual command as fallback.
    """
    import sys, subprocess, os, tempfile, threading

    # ── Windows ──────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        import ctypes, time

        port_rule = f"JARVIS Dashboard Port {port}"
        prog_rule = "JARVIS Dashboard Python"
        py_exe = sys.executable

        def _oem_encoding() -> str:
            # Консольные утилиты (netsh) пишут в OEM-кодировке (cp866 на RU),
            # а не в ANSI. Без этого reader-потоки падают с UnicodeDecodeError.
            try:
                import ctypes as _ct

                return f"cp{_ct.windll.kernel32.GetOEMCP()}"
            except Exception:
                return "cp866"

        def _netsh_rule_exists(name: str) -> bool:
            try:
                r = subprocess.run(
                    [
                        "netsh",
                        "advfirewall",
                        "firewall",
                        "show",
                        "rule",
                        f"name={name}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    encoding=_oem_encoding(),
                    errors="replace",
                )
                out = (r.stdout or "").lower()
                no_match = ("no rules match" in out) or ("не соответствует" in out)
                return r.returncode == 0 and not no_match
            except Exception:
                return False

        def _network_is_public() -> bool:
            try:
                r = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "(Get-NetConnectionProfile | "
                        "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                        "Measure-Object).Count",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=6,
                    encoding=_oem_encoding(),
                    errors="replace",
                )
                return r.stdout.strip() not in ("", "0")
            except Exception:
                return False

        need_port = not _netsh_rule_exists(port_rule)
        need_prog = not _netsh_rule_exists(prog_rule)
        need_private = _network_is_public()

        # Смена профиля сети с Public на Private — это ПОНИЖЕНИЕ защиты всей
        # системы (Public = жёсткие правила брандмауэра), а не «настройка
        # дашборда». Раньше это делалось молча при каждом старте. Теперь —
        # только по явному opt-in владельца.
        if need_private:
            if os.environ.get("JARVIS_DASHBOARD_NET_PRIVATE") == "1":
                print(
                    "[Dashboard] JARVIS_DASHBOARD_NET_PRIVATE=1: меняю профиль "
                    "сети Public → Private по твоей явной просьбе."
                )
            else:
                print(
                    "[Dashboard] ⚠ Сеть помечена как Public — дашборд может быть "
                    "недоступен с телефона. Я НЕ меняю профиль автоматически "
                    "(это понижает защиту всей системы). Если согласен — запусти "
                    "с JARVIS_DASHBOARD_NET_PRIVATE=1, либо сделай вручную: "
                    "Параметры сети → свойства → профиль «Частный»."
                )
                need_private = False

        if not need_port and not need_prog and not need_private:
            return  # already fully configured

        # Build a .bat file — netsh + powershell, runs fast when elevated
        bat_lines = ["@echo off"]
        if need_private:
            bat_lines.append(
                'powershell -NoProfile -NonInteractive -Command "'
                "Get-NetConnectionProfile | "
                "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                'Set-NetConnectionProfile -NetworkCategory Private"'
            )
        if need_port:
            bat_lines.append(
                f"netsh advfirewall firewall add rule "
                f'name="{port_rule}" protocol=TCP dir=in '
                f"localport={port} action=allow"
            )
        if need_prog:
            bat_lines.append(
                f"netsh advfirewall firewall add rule "
                f'name="{prog_rule}" dir=in action=allow '
                f'program="{py_exe}" enable=yes'
            )

        bat_body = "\r\n".join(bat_lines) + "\r\n"
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="jarvis_fw_")
        try:
            os.write(fd, bat_body.encode("mbcs"))  # Windows cmd.exe expects ANSI
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return

        # ── Try running directly (succeeds when already admin) ────────────────
        try:
            r = subprocess.run([bat_path], capture_output=True, timeout=8, shell=True)
            if r.returncode == 0:
                print(f"[Dashboard] Firewall configured for port {port}.")
                try:
                    os.unlink(bat_path)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # ── ShellExecuteW: native UAC elevation (most reliable on Windows) ────
        # ShellExecuteW with verb "runas" always shows the UAC dialog regardless
        # of UAC level settings. Non-blocking — uvicorn is already running.
        print("[Dashboard] One-time network setup required.")
        print("[Dashboard] >>> A Windows security dialog will appear — click 'Yes' <<<")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,  # hwnd  (no parent window)
                "runas",  # verb  (request elevation)
                bat_path,  # file  (our .bat)
                None,  # params
                None,  # working dir
                0,  # SW_HIDE (run without a visible cmd window)
            )
            if int(ret) > 32:
                # ShellExecuteW returns immediately; bat finishes in ~1 second.
                # Sleep briefly so the rules are in place before the first retry.
                time.sleep(2)
                print(f"[Dashboard] Network setup complete — port {port} is open.")
                print("[Dashboard] Refresh your phone browser to connect.")
            else:
                print("[Dashboard] Setup was not allowed.")
                print(
                    "[Dashboard] Phone connections may fail until JARVIS is run as Administrator."
                )
        except Exception as e:
            print(f"[Dashboard] Firewall setup error: {e}")
        finally:
            # Cleanup after the bat has had time to run
            def _cleanup(path: str) -> None:
                time.sleep(5)
                try:
                    os.unlink(path)
                except Exception:
                    pass

            threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()
        return

    # ── macOS ─────────────────────────────────────────────────────────────────
    if sys.platform == "darwin":
        fw_ctl = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        try:
            r = subprocess.run(
                [fw_ctl, "--getglobalstate"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "disabled" in r.stdout.lower():
                return  # firewall off — nothing to do

            py = sys.executable
            listed = subprocess.run(
                [fw_ctl, "--listapps"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if py in listed.stdout:
                return  # already allowed

            print(
                "[Dashboard] One-time network setup — enter your password in the macOS dialog."
            )
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'do shell script "{fw_ctl} --add {py} && {fw_ctl} --unblockapp {py}"'
                    f" with administrator privileges",
                ],
                timeout=60,
            )
        except Exception:
            pass  # macOS firewall is off by default — silent failure is fine
        return

    # ── Linux ─────────────────────────────────────────────────────────────────
    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = subprocess.run(prefix + cmd, capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    try:  # ufw
        r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            if _privileged(["ufw", "allow", f"{port}/tcp"]):
                print(f"[Dashboard] ufw: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo ufw allow {port}/tcp")
            return
    except FileNotFoundError:
        pass

    try:  # firewalld
        r = subprocess.run(
            ["firewall-cmd", "--state"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "running" in r.stdout.lower():
            ok = _privileged(
                ["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"]
            ) and _privileged(["firewall-cmd", "--reload"])
            if ok:
                print(f"[Dashboard] firewalld: port {port} allowed.")
            else:
                print(
                    f"[Dashboard] Run manually:  sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload"
                )
            return
    except FileNotFoundError:
        pass

    try:  # iptables (not persistent but works until reboot)
        r = subprocess.run(
            ["iptables", "-L", "INPUT", "-n"], capture_output=True, timeout=5
        )
        if r.returncode == 0:
            if _privileged(
                [
                    "iptables",
                    "-A",
                    "INPUT",
                    "-p",
                    "tcp",
                    "--dport",
                    str(port),
                    "-j",
                    "ACCEPT",
                ]
            ):
                print(f"[Dashboard] iptables: port {port} opened.")
            else:
                print(
                    f"[Dashboard] Run manually:  sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT"
                )
    except FileNotFoundError:
        pass  # no iptables means firewall is probably off — nothing to do


def _ensure_crypto_js() -> None:
    if _CRYPTOJS_FILE.exists():
        return
    try:
        import urllib.request

        print("[Dashboard] Downloading CryptoJS (one-time setup)…")
        # urlopen с таймаутом вместо urlretrieve: этот вызов идёт на уровне
        # модуля (см. _ensure_crypto_js() ниже), и зависший CDN-запрос
        # означал бы, что дашборд вообще не поднимается.
        with urllib.request.urlopen(_CRYPTOJS_CDN, timeout=15) as resp:
            data = resp.read()
        _CRYPTOJS_FILE.write_bytes(data)
        print("[Dashboard] CryptoJS cached — will serve locally from now on.")
    except Exception as e:
        print(f"[Dashboard] CryptoJS download failed: {e}")
        print("[Dashboard] Encryption will fall back to CDN load on client.")


threading.Thread(target=_ensure_crypto_js, daemon=True, name="crypto-js-download").start()


# ── helpers ───────────────────────────────────────────────────────────────────


def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    # Method 1: route trick (fast, works when internet is available)
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass

    # Method 2: hostname resolution (works offline on most systems)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Method 3: enumerate all interfaces (fully offline, no external deps)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _port_free(port: int, host: str) -> bool:
    """Проверить, можно ли занять порт (сокет сразу закрывается)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _choose_ports(host: str, start: int = PORT, tries: int = 20) -> tuple[int, int]:
    """Найти свободную пару портов: основной и alias-порт (start + 1).

    Второй порт нужен для ручного входа по HTTPS: Chrome сам поднимает схему
    до https, и сертификат должен быть доступен на обоих портах.
    """
    for base in range(start, start + tries):
        if _port_free(base, host) and _port_free(base + 1, host):
            return base, base + 1
    return start, start + 1


def _resolve_bind_host() -> str:
    """Вернуть адрес для bind: LAN — только по явному включению Remote.

    По умолчанию дашборд слушает 127.0.0.1: случайный человек в той же сети
    не должен видеть пульт управления ПК. Включение: переменная окружения
    ``JARVIS_DASHBOARD_REMOTE=1`` или ``dashboard_remote: true`` в
    ``config/api_keys.json``.
    """
    raw = os.environ.get("JARVIS_DASHBOARD_REMOTE", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return "0.0.0.0"
    try:
        if bool(load_config("api_keys.json").get("dashboard_remote")):
            return "0.0.0.0"
    except Exception:
        pass
    return "127.0.0.1"


def _ensure_certs() -> bool:
    """
    Make sure config/certs holds a TLS key pair, generating a self-signed one the
    first time the dashboard runs.

    The pair is deliberately NOT shipped in the repository. A private key that
    every user downloads is the same as having no private key at all: anyone can
    present a certificate that matches it. Generating locally gives each install
    its own key, costs about a second, and happens exactly once.

    Returns True when a usable pair exists afterwards; False leaves the caller on
    plain HTTP, which still works — the QR code simply encodes http:// instead.
    """
    certs = BASE_DIR / "config" / "certs"
    key_p = certs / "jarvis.key"
    crt_p = certs / "jarvis.crt"
    if key_p.exists() and crt_p.exists():
        return True

    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        print("[Dashboard] cryptography not installed — serving over plain HTTP.")
        print("[Dashboard] For HTTPS run:  pip install cryptography")
        return False

    try:
        certs.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        who = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "JARVIS Dashboard"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JARVIS"),
            ]
        )

        # The SAN has to cover every address the phone might use: the LAN IP the
        # QR code encodes, plus localhost when testing on the machine itself.
        alt = [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]
        try:
            lan = _local_ip()
            if not lan.startswith("127."):
                alt.append(x509.IPAddress(ipaddress.IPv4Address(lan)))
        except Exception:
            pass  # no LAN address resolvable — localhost entries still work

        # Timezone-aware UTC: datetime.utcnow() is deprecated from Python 3.12 on,
        # and the builder normalises aware values to UTC itself.
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(who)
            .issuer_name(who)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .sign(key, hashes.SHA256())
        )

        key_p.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        crt_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        try:
            import os as _os

            _os.chmod(key_p, 0o600)  # best effort — largely a no-op on Windows
        except Exception:
            pass

        print(
            f"[Dashboard] Generated a self-signed certificate for this machine: {certs}"
        )
        return True
    except Exception as e:
        print(
            f"[Dashboard] Certificate generation failed ({e}) — serving over plain HTTP."
        )
        return False


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ── DashboardServer ───────────────────────────────────────────────────────────


class DashboardServer:

    def __init__(self):
        self._host = _resolve_bind_host()
        self._remote = self._host != "127.0.0.1"
        self._ip = _local_ip() if self._remote else "127.0.0.1"
        self.port, self.alias_port = _choose_ports(self._host)
        self._tokens: set[str] = set()
        self._token_keys: dict[str, str] = {}  # auth_token → session_key
        self._aes_cache: dict[str, bytes] = {}  # session_key → AES bytes
        self._clients: set[WebSocket] = set()
        self._phone_audio_clients: set[WebSocket] = set()
        self._phone_media_callback = None
        self._history: list[dict] = []
        self._command_queue = asyncio.Queue()
        self._wake_callback = None
        self._connect_callback = None
        self._interrupt_callback = None
        self._upload_callback = None
        self._pending_keys: dict[str, float] = {}
        # Защита от перебора PIN: IP → (время блокировки, счётчик неудач).
        # Без этого /login принимал сколько угодно попыток подряд.
        self._login_fails: dict[str, tuple[float, int]] = self._load_login_fails()
        self._device_sessions: dict[str, dict] = self._load_device_sessions()
        self._phone_audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        # Vision is live: keep only the newest frame so a slow model never
        # accumulates seconds of stale camera footage.
        self._phone_video_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._phone_video_active = False
        self._phone_video_latest_frame: bytes | None = None
        self._phone_video_last_frame_at = 0.0
        self._uploads_dir = UPLOADS_DIR
        self._login_html = _read("login.html")
        self._app_html = _read("app.html")
        self.app = self._build_app()

    def set_phone_media_callback(self, callback) -> None:
        """Notify the desktop UI when a paired phone opens/closes media streams."""
        self._phone_media_callback = callback

    def latest_phone_video_frame(self, max_age: float = 3.0) -> bytes | None:
        """Return a fresh frame only while the explicitly enabled phone stream is live."""
        if (
            not self._phone_video_active
            or not self._phone_video_latest_frame
            or time.monotonic() - self._phone_video_last_frame_at > max_age
        ):
            return None
        return self._phone_video_latest_frame

    def _notify_phone_media(self, kind: str, active: bool) -> None:
        callback = self._phone_media_callback
        if callback is not None:
            try:
                callback(kind, active)
            except Exception:
                pass

    # ── persistent device pairing ────────────────────────────────────────

    def _load_device_sessions(self) -> dict:
        """Restore paired devices (device_token → session_key) from disk.

        Keeps Android/browser remotes paired across JARVIS restarts instead of
        forcing a new QR code every launch. Best-effort: any read problem just
        starts from an empty table.
        """
        try:
            import json

            raw = json.loads(_DEVICES_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {
                    str(k): {"session_key": str(v.get("session_key", ""))}
                    for k, v in raw.items()
                    if isinstance(v, dict) and str(v.get("session_key", ""))
                }
        except Exception:
            pass
        return {}

    def _save_device_sessions(self) -> None:
        try:
            import json

            _DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
            _DEVICES_FILE.write_text(
                json.dumps(self._device_sessions, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception:
            pass

    _LOGIN_FAILS_FILE = BASE_DIR / "config" / "dashboard_login_fails.json"

    def _load_login_fails(self) -> dict:
        try:
            import json

            raw = json.loads(self._LOGIN_FAILS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                out = {}
                for key, value in raw.items():
                    if isinstance(value, (list, tuple)) and len(value) == 2:
                        out[str(key)] = (float(value[0]), int(value[1]))
                return out
        except Exception:
            pass
        return {}

    def _save_login_fails(self) -> None:
        try:
            import json

            self._LOGIN_FAILS_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._LOGIN_FAILS_FILE.write_text(
                json.dumps(self._login_fails, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── one-time key management ───────────────────────────────────────────

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = "".join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        return key

    @staticmethod
    def _ssl_enabled() -> bool:
        certs = BASE_DIR / "config" / "certs"
        return (certs / "jarvis.key").exists() and (certs / "jarvis.crt").exists()

    def get_url(self) -> str:
        proto = "https" if self._ssl_enabled() else "http"
        return f"{proto}://{self._ip}:{self.port}"

    def get_manual_url(self) -> str:
        """URL for manual browser entry. When HTTPS active, points to alias port (also HTTPS)."""
        if self._ssl_enabled():
            return f"{self._ip}:{self.alias_port}"
        return f"{self._ip}:{self.port}"

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk:
            return None
        try:
            return _decrypt_cbc(self._aes_key(sk), enc_b64)
        except Exception:
            return None

    # ── callbacks ────────────────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    def set_interrupt_callback(self, fn) -> None:
        """fn() stops JARVIS mid-speech (phone 'СТОП РЕЧИ')."""
        self._interrupt_callback = fn

    def set_upload_callback(self, fn) -> None:
        """fn(path, mime) hands an uploaded image to JARVIS's vision."""
        self._upload_callback = fn

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def broadcast_phone_audio(self, pcm: bytes) -> None:
        """Mirror JARVIS's 24 kHz PCM reply to explicitly connected phone clients."""
        if not pcm or not self._phone_audio_clients:
            return
        dead: set[WebSocket] = set()
        for ws in tuple(self._phone_audio_clients):
            try:
                await ws.send_bytes(pcm)
            except Exception:
                dead.add(ws)
        self._phone_audio_clients.difference_update(dead)

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        def _auth(req: Request) -> bool:
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return bool(tok) and tok in self._tokens

        # serve CryptoJS from local cache, fallback to CDN redirect
        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(
                    str(_CRYPTOJS_FILE), media_type="application/javascript"
                )
            from fastapi.responses import RedirectResponse

            return RedirectResponse(_CRYPTOJS_CDN)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            # Auth is handled client-side via sessionStorage bearer token.
            # Server-side header auth can't work here because browser navigations
            # don't send custom headers (location.href doesn't carry Authorization).
            html = self._app_html.replace("__IP__", self._ip).replace(
                "__PORT__", str(self.port)
            )
            return HTMLResponse(html)

        @app.post("/login")
        async def login(req: Request):
            # Rate limit: 5 неудач подряд → блок на 5 минут по IP. PIN шестизначный,
            # и без этого /login позволял перебирать его бесконечно.
            client = req.client.host if req.client else "unknown"
            now = time.time()
            blocked_until, fails = self._login_fails.get(client, (0.0, 0))
            if now < blocked_until:
                return JSONResponse(
                    {"ok": False, "error": "Too many attempts. Try again later."},
                    status_code=429,
                )

            body = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                self._login_fails.pop(client, None)
                self._save_login_fails()
                del self._pending_keys[entered]  # one-time use
                tok = secrets.token_urlsafe(32)
                dev_tok = secrets.token_urlsafe(32)
                self._tokens.add(tok)
                self._token_keys[tok] = entered
                self._aes_key(entered)  # pre-derive & cache
                self._device_sessions[dev_tok] = {"session_key": entered}
                self._save_device_sessions()
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(
                    self.broadcast(
                        {"type": "sys", "text": "Remote connection established."}
                    )
                )
                # Bearer token in response body — no cookies needed (works on any browser/HTTP)
                return JSONResponse({"ok": True, "token": tok, "key": entered, "device_token": dev_tok})
            # Неудача: растим счётчик, после 5 подряд — блок на 5 минут.
            fails += 1
            if fails >= 5:
                self._login_fails[client] = (now + 300.0, 0)
                self._save_login_fails()
                return JSONResponse(
                    {"ok": False, "error": "Too many attempts. Try again in 5 minutes."},
                    status_code=429,
                )
            self._login_fails[client] = (0.0, fails)
            self._save_login_fails()
            return JSONResponse(
                {"ok": False, "error": "Invalid or expired key"}, status_code=401
            )

        @app.get("/auto-login")
        async def auto_login(key: str = ""):
            """QR code target — validates one-time key, creates session, redirects phone."""
            now = time.time()
            if (
                not key
                or key not in self._pending_keys
                or self._pending_keys[key] <= now
            ):
                return HTMLResponse("""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>
  *{box-sizing:border-box}body{background:radial-gradient(ellipse at 50% 0%,#16344888,transparent 36rem),#070b12;color:#f3f7fd;font-family:system-ui,sans-serif;
       display:flex;align-items:center;justify-content:center;min-height:100dvh;margin:0;padding:20px;text-align:center}
  main{max-width:420px;padding:32px 28px;background:#101827;border:1px solid #27364b;border-radius:22px;box-shadow:0 24px 72px #0008}
  h2{color:#ff7185;margin:0 0 12px;font-size:22px}p{color:#9aaac0;font-size:14px;line-height:1.6;margin:0}
</style></head>
<body><main><h2>Срок действия QR-кода истёк</h2>
<p>Откройте <strong style="color:#f3f7fd">«Удалённое управление»</strong> в JARVIS на ПК и получите новый QR-код.</p>
</main></body></html>""")

            del self._pending_keys[key]
            tok = secrets.token_urlsafe(32)
            dev_tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = key
            self._aes_key(key)
            self._device_sessions[dev_tok] = {"session_key": key}
            self._save_device_sessions()

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(
                self.broadcast(
                    {
                        "type": "sys",
                        "text": "Remote connection established via QR code.",
                    }
                )
            )

            return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>
  *{{box-sizing:border-box}}body{{background:radial-gradient(ellipse at 50% 0%,#16344888,transparent 36rem),#070b12;color:#f3f7fd;font-family:system-ui,sans-serif;
       display:flex;align-items:center;justify-content:center;min-height:100dvh;margin:0;padding:20px;text-align:center}}
  main{{max-width:420px;padding:32px 28px;background:#101827;border:1px solid #27364b;border-radius:22px;box-shadow:0 24px 72px #0008}}
  p{{color:#b2c0d4;font-size:15px;line-height:1.6;margin:0}}
</style></head>
<body><main>
<script>
  sessionStorage.setItem('jarvis_token','{tok}');
  sessionStorage.setItem('jarvis_key','{key}');
  localStorage.setItem('jarvis_device_token','{dev_tok}');
  setTimeout(function(){{location.replace('/')}},400);
</script>
<p>Подключаю планшет к JARVIS…</p>
</main></body></html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            """Return a fresh auth token for a previously paired device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            dev_tok = (body.get("device_token") or "").strip()
            if not dev_tok or dev_tok not in self._device_sessions:
                return JSONResponse({"ok": False}, status_code=401)
            session_key = self._device_sessions[dev_tok]["session_key"]
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = session_key
            self._aes_key(session_key)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(
                self.broadcast(
                    {"type": "sys", "text": "Known device reconnected automatically."}
                )
            )
            return JSONResponse({"ok": True, "token": tok, "key": session_key})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            """Invalidate all persistent device tokens (admin action)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            count = len(self._device_sessions)
            self._device_sessions.clear()
            self._save_device_sessions()
            return JSONResponse({"ok": True, "revoked": count})

        @app.post("/api/command")
        async def command(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc = body.get("enc", "")
            if enc:
                text = self._decrypt(token, enc)
                if text is None:
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                text = (body.get("text") or "").strip()
            if text:
                await self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/interrupt")
        async def interrupt_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._interrupt_callback:
                self._interrupt_callback()
            return JSONResponse({"ok": True})

        # ── Ollama: статус и ручная проверка подключения ────────────────
        @app.get("/api/ollama/status")
        async def ollama_status(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                from core import gemini as _gemini

                available = _gemini.ollama_available()
                model = _gemini.ollama_model() if available else ""
                base_url = _gemini.ollama_base_url()
                return JSONResponse(
                    {
                        "ok": True,
                        "available": available,
                        "model": model,
                        "base_url": base_url,
                    }
                )
            except Exception as exc:
                return JSONResponse(
                    {"ok": False, "error": f"Ollama-модуль недоступен: {exc}"},
                    status_code=500,
                )

        @app.post("/api/ollama/check")
        async def ollama_check(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                from core import gemini as _gemini

                available = _gemini.ollama_available()
                model = _gemini.ollama_model()
                return JSONResponse(
                    {
                        "ok": True,
                        "available": available,
                        "model": model,
                        "message": "Ollama отвечает"
                        if available
                        else "Ollama не отвечает (проверьте, что сервер запущен на 127.0.0.1:11434)",
                    }
                )
            except Exception as exc:
                return JSONResponse(
                    {"ok": False, "error": f"Проверка не удалась: {exc}"},
                    status_code=500,
                )

        # ── Telegram: новые сообщения личного аккаунта ────────────────
        # Только чтение через уже настроенный Telethon-клиент владельца
        # (mcp_servers/telegram_personal). Состояние вотча не трогаем:
        # чекпоинты briefing_state.json меняет только main.py.

        @app.get("/api/telegram/recent")
        async def telegram_recent(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            import json as _json
            try:
                from mcp_servers.telegram_personal.server import (
                    telegram_read_updates,
                )
            except Exception as exc:
                return JSONResponse(
                    {"ok": False, "error": f"Telegram-модуль недоступен: {exc}"}
                )
            try:
                raw = await telegram_read_updates(
                    since_json="{}", chat_limit=10, message_limit=5
                )
                data = _json.loads(raw) if isinstance(raw, str) else {}
            except Exception as exc:
                return JSONResponse({"ok": False, "error": f"Не прочиталось: {exc}"})
            if data.get("error"):
                return JSONResponse({"ok": False, "error": str(data["error"])})
            msgs = []
            for m in (data.get("messages") or [])[:15]:
                text = str(m.get("text") or "").strip()
                media_type = str(m.get("media_type") or "").strip()
                if not text and media_type:
                    text = "[%s]" % media_type
                if not text:
                    continue
                msgs.append({
                    "chat": str(m.get("chat", "?"))[:60],
                    "from": str(m.get("sender", "?"))[:40],
                    "text": text[:300],
                    "date": str(m.get("date", ""))[:25],
                    "media_type": media_type or None,
                })
            return JSONResponse({"ok": True, "messages": msgs})

        # ── Autonomy: дневник развития ───────────────────────────────
        # Только чтение дневника; действия — через POST /api/autonomy.

        @app.get("/api/autonomy/diary")
        async def autonomy_diary(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                from autonomy import journal as _journal
            except Exception as exc:
                return JSONResponse({"ok": False, "error": f"Нет модуля: {exc}"})
            return JSONResponse({"ok": True, "text": _journal.today_text()})

        @app.post("/api/autonomy")
        async def autonomy_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                body = {}
            op = str(body.get("op", "status")).strip().lower()
            if op not in ("status", "repair", "scout", "adopt", "journal",
                          "optimize", "cycle"):
                return JSONResponse({"ok": False,
                                     "error": "op: status|repair|scout|adopt|journal|optimize|cycle"})
            if op == "adopt" and "/" not in str(body.get("repo", "")):
                return JSONResponse({"ok": False,
                                     "error": "adopt: нужно точное owner/repo"})
            try:
                from actions.autonomy import autonomy as _autonomy
            except Exception as exc:
                return JSONResponse(
                    {"ok": False, "error": f"Модуль автономности недоступен: {exc}"}
                )
            try:
                params = {"op": op}
                for key in ("query", "repo"):
                    if body.get(key):
                        params[key] = str(body[key])
                result = await asyncio.to_thread(_autonomy, params)
                return JSONResponse({"ok": True, "result": result})
            except RuntimeError as exc:
                return JSONResponse({"ok": False, "error": str(exc)})
            except Exception as exc:
                return JSONResponse({"ok": False, "error": f"Ошибка: {exc}"})

        @app.post("/api/phone/mic")
        async def phone_mic_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                body = {}
            op = str(body.get("op", "status")).strip().lower()
            mapping = {"live": "listen_live", "listen": "listen_live",
                       "record": "record", "stop": "stop", "status": "status"}
            action = mapping.get(op, "status")
            params = {"action": action,
                      "device_id": str(body.get("device_id", "") or "")}
            if action == "listen_live":
                src = str(body.get("source", "mic") or "mic")
                params["source"] = src if src in ("mic", "output") else "mic"
            if action == "record":
                try:
                    params["seconds"] = int(body.get("seconds", 10) or 10)
                except (TypeError, ValueError):
                    params["seconds"] = 10
            try:
                from actions.phone_mic import phone_mic as _phone_mic
            except Exception as exc:
                return JSONResponse(
                    {"ok": False, "error": f"Модуль микрофона недоступен: {exc}"}
                )
            try:
                result = await asyncio.to_thread(_phone_mic, params)
                return JSONResponse({"ok": True, "result": result})
            except RuntimeError as exc:
                return JSONResponse({"ok": False, "error": str(exc)})
            except Exception as exc:
                return JSONResponse({"ok": False, "error": f"Ошибка: {exc}"})

        # ── Phone mic real-time audio → Gemini Live ──────────────────────────

        @app.websocket("/ws/phone-video")
        async def phone_video_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._notify_phone_media("video_connecting", True)
            asyncio.create_task(
                self.broadcast({"type": "sys", "text": "Android camera channel connected; waiting for the first frame."})
            )
            first_frame_received = False
            last_ack_at = 0.0
            try:
                while True:
                    frame = await websocket.receive_bytes()
                    if not frame or len(frame) > 2 * 1024 * 1024:
                        continue
                    now = time.monotonic()
                    self._phone_video_latest_frame = frame
                    self._phone_video_last_frame_at = now
                    if self._phone_video_queue.full():
                        try:
                            self._phone_video_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    try:
                        self._phone_video_queue.put_nowait(frame)
                    except asyncio.QueueFull:
                        pass
                    if not first_frame_received:
                        first_frame_received = True
                        self._phone_video_active = True
                        self._notify_phone_media("video", True)
                        asyncio.create_task(
                            self.broadcast({"type": "sys", "text": "Android camera frame received by JARVIS."})
                        )
                    if now - last_ack_at >= 5.0:
                        await websocket.send_text("frame_received")
                        last_ack_at = now
            except WebSocketDisconnect:
                pass
            finally:
                if first_frame_received:
                    self._phone_video_active = False
                self._notify_phone_media("video", False)
                asyncio.create_task(
                    self.broadcast({"type": "sys", "text": "Live phone camera stopped."})
                )

        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._phone_audio_clients.add(websocket)
            self._notify_phone_media("audio", True)
            asyncio.create_task(
                self.broadcast({"type": "sys", "text": "Phone microphone live."})
            )
            audio_seen = False
            try:
                while True:
                    data = await websocket.receive_bytes()
                    if data and not audio_seen:
                        audio_seen = True
                        self._notify_phone_media("audio_data", True)
                    try:
                        self._phone_audio_queue.put_nowait(
                            {"data": data, "mime_type": "audio/pcm;rate=16000"}
                        )
                    except asyncio.QueueFull:
                        pass  # drop frame rather than block
            except WebSocketDisconnect:
                pass
            finally:
                self._phone_audio_clients.discard(websocket)
                if audio_seen:
                    self._notify_phone_media("audio_data", False)
                self._notify_phone_media("audio", bool(self._phone_audio_clients))
                asyncio.create_task(
                    self.broadcast({"type": "sys", "text": "Phone microphone stopped."})
                )

        # ── File sharing ──────────────────────────────────────────────────────

        def _safe_filename(raw: str) -> str:
            name = Path(raw).name  # strip path components
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")
            return name or "upload"

        if _UPLOAD_OK:

            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not _auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = _safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {
                                        "error": f"File too large (max {MAX_UPLOAD_MB} MB)"
                                    },
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(
                    self.broadcast(
                        {
                            "type": "file_received",
                            "name": dest.name,
                            "size": size,
                            "saved_to": str(self._uploads_dir),
                        }
                    )
                )
                # An uploaded image is something the user wants JARVIS to
                # actually look at — hand it to the vision pipeline.
                if self._upload_callback:
                    _ext = Path(dest.name).suffix.lower().lstrip(".")
                    _mime = {
                        "jpg": "image/jpeg",
                        "jpeg": "image/jpeg",
                        "png": "image/png",
                        "webp": "image/webp",
                        "heic": "image/heic",
                        "heif": "image/heif",
                    }.get(_ext)
                    if _mime:
                        try:
                            self._upload_callback(str(dest), _mime)
                        except Exception as _cbx:
                            print(f"[Dashboard] Upload callback failed: {_cbx}")
                return JSONResponse({"ok": True, "name": dest.name, "size": size})

        else:

            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception:
                pass
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, token: str = ""):
            # Auth via query param — browser <a download> can't send custom headers
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r"[/\\]", "", filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe)

        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "command":
                        enc = data.get("enc", "")
                        t = (
                            self._decrypt(tok, enc)
                            if enc
                            else (data.get("text") or "").strip()
                        )
                        if t:
                            await self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)

        return app

    # ── serve ─────────────────────────────────────────────────────────────

    async def _serve_alias(self) -> None:
        """Second HTTPS server on the alias port sharing the same app and state.
        Chrome HTTPS-upgrades any bare IP:PORT the user types, so this port also needs TLS.
        User types IP:8001 → Chrome tries https → self-signed cert warning → accept once → done.
        """
        ssl_key = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"
        if self._remote:
            asyncio.get_event_loop().run_in_executor(
                None, _ensure_network_access, self.alias_port)
        cfg = uvicorn.Config(
            self.app,
            host=self._host,
            port=self.alias_port,
            log_level="warning",
            ssl_keyfile=str(ssl_key),
            ssl_certfile=str(ssl_cert),
        )
        print(
            f"[Dashboard] Manual entry:  {self._ip}:{self.alias_port}  "
            "(type in browser, accept cert once)"
        )
        await uvicorn.Server(cfg).serve()

    async def serve(self) -> None:
        if not _DEPS_OK:
            import sys as _sys

            print("[Dashboard] fastapi/uvicorn not installed — dashboard disabled.")
            print(f"[Dashboard] причина: {_DEPS_ERROR or '?'} "
                  f"(exe={_sys.executable})")
            print(
                "[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography"
            )
            return

        # Firewall setup runs in a thread — uvicorn starts immediately,
        # no waiting for UAC dialogs or subprocess timeouts.
        if self._remote:
            asyncio.get_event_loop().run_in_executor(
                None, _ensure_network_access, self.port)

        # Generate the TLS pair on first run so no private key ships in the repo.
        _ensure_certs()

        use_ssl = self._ssl_enabled()
        ssl_key = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"

        if use_ssl:
            asyncio.create_task(self._serve_alias())

        cfg = uvicorn.Config(
            self.app,
            host=self._host,
            port=self.port,
            log_level="warning",
            **(
                {"ssl_keyfile": str(ssl_key), "ssl_certfile": str(ssl_cert)}
                if use_ssl
                else {}
            ),
        )

        proto = "https" if use_ssl else "http"
        if self.port != PORT:
            print(f"[Dashboard] Port {PORT} занят — использую {self.port}.")
        print(f"[Dashboard] {proto}://{self._ip}:{self.port}")
        if self._remote:
            print("[Dashboard] Remote включён: дашборд доступен в локальной сети.")
        else:
            print("[Dashboard] Только этот ПК. Для LAN: JARVIS_DASHBOARD_REMOTE=1.")
        print("[Dashboard] Press 'Remote Control' in JARVIS UI to get the QR code.")
        await uvicorn.Server(cfg).serve()
