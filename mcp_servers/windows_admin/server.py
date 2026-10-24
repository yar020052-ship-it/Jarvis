"""Windows administration MCP tools for the user's own JARVIS installation."""

from __future__ import annotations

import json
import platform
import re
import subprocess
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import ToolAnnotations


_server = MCPServer("JARVIS Windows administration")
_WINDOWS = platform.system() == "Windows"
_HIDE = {"creationflags": subprocess.CREATE_NO_WINDOW} if _WINDOWS else {}
_PROTECTED_SERVICES = {
    "appidsvc", "bfe", "cryptsvc", "dcomlaunch", "dhcp", "dnscache",
    "eventlog", "gpsvc", "lanmanserver", "lanmanworkstation", "lsm",
    "mpssvc", "nlasvc", "plugplay", "power", "rpcss", "samss",
    "schedule", "winmgmt", "wuauserv",
}


def _require_windows() -> str | None:
    if not _WINDOWS:
        return "Windows administration tools are available only on Windows."
    return None


def _run(argv: list[str], timeout: int = 45) -> dict:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, **_HIDE,
        )
        return {
            "exit_code": result.returncode,
            "stdout": (result.stdout or "")[:16000],
            "stderr": (result.stderr or "")[:4000],
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command exceeded the {timeout}-second time limit."}
    except OSError as exc:
        return {"error": f"Could not start command: {exc}"}


@_server.tool(annotations=ToolAnnotations(read_only_hint=True))
async def list_services(filter: str = "") -> str:
    """List Windows services and their state/start mode; read-only."""
    error = _require_windows()
    if error:
        return error
    term = str(filter or "").strip()[:80].replace("'", "''")
    selector = (
        f"$s = $s | Where-Object {{ $_.Name -like '*{term}*' -or $_.DisplayName -like '*{term}*' }}; "
        if term else ""
    )
    script = (
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding = "
        "[System.Text.UTF8Encoding]::new(); "
        "$s = Get-CimInstance Win32_Service; " + selector +
        "$s | Sort-Object Name | Select-Object -First 120 Name,DisplayName,State,StartMode "
        "| ConvertTo-Json -Compress"
    )
    result = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script])
    return json.dumps(result, ensure_ascii=False)


@_server.tool(annotations=ToolAnnotations(read_only_hint=True))
async def list_drivers() -> str:
    """List installed third-party Windows driver packages with pnputil; read-only."""
    error = _require_windows()
    if error:
        return error
    result = _run(["pnputil.exe", "/enum-drivers"], timeout=60)
    return json.dumps(result, ensure_ascii=False)


@_server.tool()
async def change_service(service_name: str, action: str) -> str:
    """Start/stop a named service or change its startup mode. Mutations are controlled by this server's MCP access policy."""
    error = _require_windows()
    if error:
        return error
    name = str(service_name or "").strip()
    operation = str(action or "").strip().lower()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
        return "Provide an exact Windows service Name from list_services."
    if operation not in {"start", "stop", "enable", "disable"}:
        return "action must be start, stop, enable, or disable."
    if operation in {"stop", "disable"} and name.casefold() in _PROTECTED_SERVICES:
        return f"Refusing to stop or disable protected Windows service '{name}'."

    quoted = name.replace("'", "''")
    scripts = {
        "start": f"Start-Service -Name '{quoted}' -ErrorAction Stop",
        "stop": f"Stop-Service -Name '{quoted}' -ErrorAction Stop",
        "enable": f"Set-Service -Name '{quoted}' -StartupType Automatic -ErrorAction Stop",
        "disable": f"Set-Service -Name '{quoted}' -StartupType Disabled -ErrorAction Stop",
    }
    script = (
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding = "
        "[System.Text.UTF8Encoding]::new(); "
        f"{scripts[operation]}; Get-CimInstance Win32_Service -Filter \"Name='{quoted}'\" "
        "| Select-Object Name,DisplayName,State,StartMode | ConvertTo-Json -Compress"
    )
    return json.dumps(_run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]), ensure_ascii=False)


@_server.tool()
async def reinstall_driver(inf_path: str) -> str:
    """Install/update a driver from a specific local INF package using pnputil; requires an existing absolute .inf path."""
    error = _require_windows()
    if error:
        return error
    path = Path(str(inf_path or "").strip()).expanduser()
    if not path.is_absolute() or path.suffix.lower() != ".inf" or not path.is_file():
        return "Provide an existing absolute path to the intended driver .inf file."
    result = _run(["pnputil.exe", "/add-driver", str(path), "/install"], timeout=120)
    if result.get("exit_code") not in (None, 0):
        result["note"] = "If Windows reports access denied, run JARVIS with administrator rights yourself; this tool does not elevate or bypass UAC."
    return json.dumps(result, ensure_ascii=False)


@_server.tool()
async def run_command(shell: str, command: str, timeout_seconds: int = 45) -> str:
    """Run a user-requested PowerShell or CMD command as the current Windows user; MCP access policy controls confirmation."""
    error = _require_windows()
    if error:
        return error
    command = str(command or "").strip()
    if not command or len(command) > 16000:
        return "Provide a command between 1 and 16000 characters."
    shell = str(shell or "powershell").strip().lower()
    try:
        timeout = max(1, min(120, int(timeout_seconds or 45)))
    except (TypeError, ValueError):
        return "timeout_seconds must be a number between 1 and 120."
    if shell in {"powershell", "pwsh"}:
        argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
    elif shell in {"cmd", "cmd.exe"}:
        argv = ["cmd.exe", "/d", "/c", "chcp 65001>nul & " + command]
    else:
        return "shell must be powershell or cmd."
    result = _run(argv, timeout=timeout)
    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    _server.run()
