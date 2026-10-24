"""Self-management of JARVIS startup extras by voice.

Two layers:

1. Direct control — status/enable/disable/start/stop for each extra:
   error_bot (Telegram error poller), telegramai (TelegramAI scheduled
   task), dashboard (phone dashboard autostart flag), openai_docs (MCP).

2. Autonomous layer — action="ensure" with need=<goal>. The code itself
   figures out what the goal requires, starts/enables what is missing,
   and reports whether a JARVIS restart is still needed. Prefer this when
   the user just states a goal ("check Telegram", "phone control",
   "report errors") instead of naming a component.

action="restart", target="jarvis" relaunches JARVIS through
launch_hidden.vbs and only exits the current process after a successor
`main.py` process is confirmed — so it never kills JARVIS without a
replacement. Needed after dashboard/MCP (re)enables.

Auto-discovered by core.action_loader: one TOOL dict + one handler.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

_OS = platform.system()
_ON_WIN = _OS == "Windows"
_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if _ON_WIN else {}

_TELEGRAMAI_TASK = "TelegramAI Bot"
_OPENAI_DOCS = "OpenAI_Docs"
_TELEGRAM_MCP = "telegram_personal"
_DASHBOARD_ENV = "JARVIS_ENABLE_DASHBOARD"
_RESTART_NOTE = "Применится после перезапуска JARVIS (action=restart, target=jarvis)."


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    if _ON_WIN:
        try:
            import ctypes

            oem = ctypes.windll.kernel32.GetOEMCP()
            return raw.decode(f"cp{oem}", errors="replace")
        except Exception:
            pass
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return raw.decode(enc)
        except ValueError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **_HIDE)
        return r.returncode, _decode(r.stdout).strip(), _decode(r.stderr).strip()
    except Exception as e:
        return -1, "", str(e)


# -- process helpers ----------------------------------------------------------

def _poller_pids() -> list[int]:
    try:
        import psutil
    except ImportError:
        return []
    out = []
    try:
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
            except Exception:
                continue
            if "error_bot_poller.py" in cmd.replace("\\", "/"):
                out.append(p.info["pid"])
    except Exception:
        pass
    return out


def _main_pids(exclude_self: bool = True) -> list[int]:
    """PIDs of running JARVIS main.py processes (used by restart)."""
    try:
        import psutil
    except ImportError:
        return []
    me = os.getpid()
    out = []
    try:
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or []).replace("\\", "/")
            except Exception:
                continue
            if "main.py" in cmd and "startup_selfmanage" not in cmd:
                if exclude_self and p.info["pid"] == me:
                    continue
                out.append(p.info["pid"])
    except Exception:
        pass
    return out


# -- Telegram remote bridge (plugins/telegram_remote, in-process) ----------------
# NOTE: the standalone error_bot_poller.py scripts are RETIRED (one bot token
# allows exactly one getUpdates consumer — a second poller steals updates and
# the bridge dies with 409). Never launch a separate poller process again.

def _remote() -> tuple[bool, str]:
    """Import the plugin module. Returns (ok, module-or-error)."""
    try:
        import plugins.telegram_remote as _tr
        return True, _tr
    except Exception as e:
        return False, f"telegram_remote plugin unavailable: {e}"


def _stray_poller_pids() -> list[int]:
    """PIDs of stray standalone error_bot_poller.py processes (update thieves)."""
    return _poller_pids()


def _kill_strays() -> str:
    pids = _stray_poller_pids()
    if not pids:
        return ""
    try:
        import psutil
    except ImportError:
        return f"found stray poller PIDs {pids}, no psutil to stop them."
    killed = 0
    for pid in pids:
        try:
            psutil.Process(pid).terminate()
            killed += 1
        except Exception:
            pass
    return f"убрал посторонних поллеров: {killed}/{len(pids)}." if killed else "не смог остановить посторонний поллер."


# -- dashboard flag (launch_hidden.vbs, applies on next launch) ---------------

def _vbs_path() -> Path:
    return _base_dir() / "launch_hidden.vbs"


def _dashboard_autostart() -> bool | None:
    try:
        t = _vbs_path().read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'\("JARVIS_ENABLE_DASHBOARD"\)\s*=\s*"([01])"', t)
    return m.group(1) == "1" if m else None


def _set_dashboard_autostart(on: bool) -> str:
    p = _vbs_path()
    try:
        t = p.read_text(encoding="utf-8")
    except OSError as e:
        return f"VBS not found: {e}"
    val = "1" if on else "0"
    nt, n = re.subn(
        r'(\("JARVIS_ENABLE_DASHBOARD"\)\s*=\s*")[01](")',
        rf"\g<1>{val}\g<2>",
        t,
        count=1,
    )
    if n == 0:
        return "Dashboard flag line not found in launch_hidden.vbs"
    try:
        p.write_text(nt, encoding="utf-8")
    except OSError as e:
        return f"Write failed: {e}"
    return ""


# -- MCP config (delegates to mcp_manager) ------------------------------------

def _mcp_enabled(name: str) -> bool | None:
    try:
        d = json.loads((_base_dir() / "config" / "mcp_servers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for s in d.get("servers", []):
        if isinstance(s, dict) and s.get("name") == name:
            return bool(s.get("enabled"))
    return None


def _set_mcp(name: str, on: bool) -> str:
    """Returns '' on success, error text otherwise. Idempotent."""
    cur = _mcp_enabled(name)
    if cur is None:
        return f"MCP server '{name}' not found in config."
    if cur == on:
        return ""
    try:
        from actions.mcp_manager import mcp_manager as _mm
    except ImportError as e:
        return f"mcp_manager unavailable: {e}"
    try:
        res = str(_mm({"action": "enable" if on else "disable", "name": name}))
    except Exception as e:
        return f"mcp_manager error: {e}"
    return "" if res.startswith("Saved MCP configuration") else res


# -- TelegramAI scheduled task ------------------------------------------------

def _task_state() -> str:
    code, so, se = _run(["schtasks", "/query", "/tn", _TELEGRAMAI_TASK, "/fo", "list"])
    if code != 0:
        return f"not found ({se[:120]})"
    m = re.search(r"(Состояние|State)\s*:\s*(\S+)", so)
    return m.group(2) if m else "unknown"


def _task_disabled(state: str) -> bool:
    return state.lower().startswith(("отключ", "disabled"))


# -- primitive ops: the Telegram remote bridge (in-process plugin, single poller) --

def _remote_autostart_cfg(on: bool) -> str:
    """Persist plugin start_on_launch. Returns '' on success."""
    p = _base_dir() / "config" / "api_keys.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"config unreadable: {e}"
    try:
        d.setdefault("plugin_config", {}).setdefault("telegram_remote", {})["start_on_launch"] = bool(on)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    except OSError as e:
        return f"config write failed: {e}"
    return ""


def _eb_status() -> str:
    ok, tr = _remote()
    if not ok:
        return tr
    try:
        st = str(tr.run({"action": "status"}))
    except Exception as e:
        return f"remote status failed: {e}"
    strays = _stray_poller_pids()
    if strays:
        st += f" ВНИМАНИЕ: посторонний поллер {strays} крадёт обновления (409)."
    return st


def _eb_start(player=None) -> str:
    killed = _kill_strays()
    ok, tr = _remote()
    if not ok:
        return f"error: {tr}"
    try:
        res = str(tr.run({"action": "start"}, player=player))
    except Exception as e:
        return f"error: start failed: {e}"
    if "already listening" in res:
        return "already" + (f" ({killed})" if killed else "")
    if "no interface" in res:
        return "error: remote needs UI player (запусти из живого JARVIS)."
    if killed and "live on" in res:
        return f"started ({killed})"
    if "live on" in res:
        return "started"
    return f"error: {res[:200]}"


def _eb_stop(player=None) -> str:
    killed = _kill_strays()
    ok, tr = _remote()
    if not ok:
        return f"error: {tr}"
    try:
        res = str(tr.run({"action": "stop"}, player=player))
    except Exception as e:
        return f"error: stop failed: {e}"
    if "not running" in res:
        return "already" + (f" ({killed})" if killed else "")
    return f"stopped ({killed})" if killed else "stopped"


def _eb_enable(player=None) -> str:
    err = _remote_autostart_cfg(True)
    if err:
        return f"error: {err}"
    r = _eb_start(player=player)
    if r.startswith("error"):
        return f"автозапуск включён, но {r}"
    return "enabled"


def _eb_disable(player=None) -> str:
    err = _remote_autostart_cfg(False)
    if err:
        return f"error: {err}"
    r = _eb_stop(player=player)
    if r.startswith("error"):
        return f"автозапуск выключен, но {r}"
    return "disabled"


# -- ensure: goal -> prerequisites, decided in code ---------------------------

def _ensure(need: str, player=None) -> str:
    need = (need or "").strip().lower()
    if need in ("telegram", "telegram_messages", "tg"):
        cur = _mcp_enabled(_TELEGRAM_MCP)
        if cur is None:
            return f"{_TELEGRAM_MCP} нет в конфиге — Telegram проверить не могу."
        if cur:
            return "Telegram готов: MCP telegram_personal включён."
        err = _set_mcp(_TELEGRAM_MCP, True)
        if err:
            return f"Не смог включить telegram_personal: {err}"
        return f"Telegram MCP был выключен — включил. {_RESTART_NOTE}"
    if need in ("phone", "dashboard", "remote", "qr"):
        cur = _dashboard_autostart()
        if cur is None:
            return "launch_hidden.vbs не найден — dashboard настроить не могу."
        if cur:
            return "Dashboard готов: автозапуск включён."
        err = _set_dashboard_autostart(True)
        if err:
            return f"Не смог включить dashboard: {err}"
        return f"Dashboard был выключен — включил. {_RESTART_NOTE}"
    if need in ("errors", "error_reports", "errorbot", "remote", "bot"):
        killed = _kill_strays()
        r = _eb_start(player=player)
        if r.startswith("error"):
            return r + (f" ({killed})" if killed else "")
        if r == "already":
            return "Связь с ботом активна: remote слушает." + (f" {killed}" if killed else "")
        return "Связь с ботом восстановлена: remote запущен." + (f" {killed}" if killed else "")
    if need in ("docs", "openai_docs", "documentation"):
        cur = _mcp_enabled(_OPENAI_DOCS)
        if cur is None:
            return f"{_OPENAI_DOCS} нет в конфиге."
        if cur:
            return "OpenAI Docs готовы: MCP включён."
        err = _set_mcp(_OPENAI_DOCS, True)
        if err:
            return f"Не смог включить OpenAI_Docs: {err}"
        return f"OpenAI Docs были выключены — включил. {_RESTART_NOTE}"
    if need in ("telegramai", "telegram_ai", "bot"):
        st = _task_state()
        if "not found" in st:
            return "Задача TelegramAI Bot не найдена в планировщике."
        if not _task_disabled(st):
            code, _, se = _run(["schtasks", "/run", "/tn", _TELEGRAMAI_TASK])
            return "TelegramAI уже включён, запустил задачу." if code == 0 else f"TelegramAI включён, но запуск не удался: {se[:150]}"
        code, _, se = _run(["schtasks", "/change", "/tn", _TELEGRAMAI_TASK, "/enable"])
        if code != 0:
            return f"Не смог включить TelegramAI: {se[:150]}"
        code, _, se = _run(["schtasks", "/run", "/tn", _TELEGRAMAI_TASK])
        return "TelegramAI был выключен — включил и запустил." if code == 0 else "TelegramAI включён (запуск при входе), разовый старт не удался."
    return "need must be telegram | phone | errors | docs | telegramai."


# -- restart: relaunch via VBS, exit only after successor confirmed -----------

def _restart() -> str:
    if not _ON_WIN:
        return "Only supported on Windows."
    # NOTE: relaunch via pythonw directly, NOT via launch_hidden.vbs — the
    # running instance holds logs/console.log open (stdout redirect), and a
    # VBS child cmd with `>> console.log` fails to open it while the lock
    # is held. pythonw has no console and needs no log file.
    pw = _base_dir() / ".venv" / "Scripts" / "pythonw.exe"
    if pw.exists():
        cmd = [str(pw), "main.py"]
    else:
        vbs = _vbs_path()
        if not vbs.exists():
            return "No pythonw.exe and no launch_hidden.vbs — cannot restart safely."
        cmd = ["wscript.exe", "//B", "//NoLogo", str(vbs)]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(_base_dir()),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        return f"Relaunch failed, staying alive: {e}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if _main_pids(exclude_self=True):
            break
        time.sleep(1)
    if not _main_pids(exclude_self=True):
        return "Новый процесс JARVIS не появился за 30с — остаюсь в живых, перезапуск отменён."
    print("[startup_selfmanage] successor confirmed, exiting for restart.")
    time.sleep(1)
    os._exit(0)
    return "restarting"  # unreachable


# -- status -------------------------------------------------------------------

def _status(target: str) -> str:
    lines = []
    if target in ("all", "error_bot"):
        lines.append("Telegram-бот (remote): " + _eb_status())
    if target in ("all", "telegramai"):
        lines.append(f"TelegramAI Bot: задача {_task_state()}")
    if target in ("all", "dashboard"):
        f = _dashboard_autostart()
        lines.append(f"Dashboard: автозапуск {'вкл' if f else 'выкл' if f is not None else 'VBS не найден'}")
    if target in ("all", "openai_docs"):
        e = _mcp_enabled(_OPENAI_DOCS)
        lines.append(f"OpenAI_Docs MCP: {'вкл' if e else 'выкл' if e is not None else 'нет в конфиге'}")
    if target in ("all", "telegram"):
        e = _mcp_enabled(_TELEGRAM_MCP)
        lines.append(f"Telegram MCP: {'вкл' if e else 'выкл' if e is not None else 'нет в конфиге'}")
    return "\n".join(lines) if lines else "Unknown target."


# -- handler ------------------------------------------------------------------

def startup_selfmanage(parameters: dict | None = None, **_ctx) -> str:
    p = parameters or {}
    action = str(p.get("action") or "status").strip().lower()
    target = str(p.get("target") or "all").strip().lower()
    if target in ("telegram_ai", "telegram-ai", "telegram_bot"):
        target = "telegramai"
    if not _ON_WIN:
        return "Only supported on Windows."

    if action == "ensure":
        return _ensure(str(p.get("need") or target if target != "all" else p.get("need") or ""),
                       player=_ctx.get("player"))

    if action == "restart" and target in ("jarvis", "all"):
        return _restart()

    if action == "status":
        return _status(target if target != "all" else "all")

    if target == "error_bot":
        player = _ctx.get("player")
        if action == "start":
            r = _eb_start(player=player)
            return "Связь с ботом уже активна." if r == "already" else (r if r.startswith("error") else "Связь с ботом запущена.")
        if action == "stop":
            r = _eb_stop(player=player)
            return "Связь с ботом уже остановлена." if r == "already" else (r if r.startswith("error") else "Связь с ботом остановлена.")
        if action == "enable":
            r = _eb_enable(player=player)
            return "Автозапуск связи с ботом включён." if r == "enabled" else r
        if action == "disable":
            r = _eb_disable(player=player)
            return "Автозапуск связи с ботом выключен." if r == "disabled" else r
        return "action must be status | start | stop | enable | disable | ensure | restart."

    if target == "telegramai":
        if action == "enable":
            code, _, se = _run(["schtasks", "/change", "/tn", _TELEGRAMAI_TASK, "/enable"])
            return "Автозапуск TelegramAI включён." if code == 0 else f"Enable failed: {se[:200]}"
        if action == "disable":
            code, _, se = _run(["schtasks", "/change", "/tn", _TELEGRAMAI_TASK, "/disable"])
            return "Автозапуск TelegramAI выключен." if code == 0 else f"Disable failed: {se[:200]}"
        if action in ("start", "run"):
            code, _, se = _run(["schtasks", "/run", "/tn", _TELEGRAMAI_TASK])
            return "TelegramAI запущен." if code == 0 else f"Run failed: {se[:200]}"
        if action == "stop":
            code, _, se = _run(["schtasks", "/end", "/tn", _TELEGRAMAI_TASK])
            return "TelegramAI остановлен." if code == 0 else f"Stop failed: {se[:200]}"
        return "action must be status | enable | disable | start | stop | ensure."

    if target == "dashboard":
        if action == "enable":
            err = _set_dashboard_autostart(True)
            return f"Включил dashboard. {_RESTART_NOTE}" if not err else err
        if action == "disable":
            err = _set_dashboard_autostart(False)
            return f"Выключил dashboard. {_RESTART_NOTE}" if not err else err
        return "action must be status | enable | disable | ensure."

    if target == "openai_docs":
        if action == "enable":
            err = _set_mcp(_OPENAI_DOCS, True)
            return f"Включил OpenAI_Docs. {_RESTART_NOTE}" if not err else err
        if action == "disable":
            err = _set_mcp(_OPENAI_DOCS, False)
            return f"Выключил OpenAI_Docs. {_RESTART_NOTE}" if not err else err
        return "action must be status | enable | disable | ensure."

    if target == "telegram":
        if action == "enable":
            err = _set_mcp(_TELEGRAM_MCP, True)
            return f"Включил Telegram MCP. {_RESTART_NOTE}" if not err else err
        if action == "disable":
            err = _set_mcp(_TELEGRAM_MCP, False)
            return f"Выключил Telegram MCP. {_RESTART_NOTE}" if not err else err
        return "action must be status | enable | disable | ensure."

    if target == "jarvis":
        return "action must be status | restart."

    return "target must be all | error_bot | telegramai | dashboard | openai_docs | telegram | jarvis."


TOOL = {
    "name": "startup_selfmanage",
    "description": (
        "Manage JARVIS startup extras by voice (Windows). COMPONENTS: "
        "error_bot = link with JARVIS's own Telegram bot via the in-process "
        "telegram_remote bridge (single getUpdates poller; the standalone "
        "error_bot_poller.py scripts are retired — NEVER launch a separate "
        "poller, it causes 409 conflicts and kills the link); "
        "telegramai = separate TelegramAI scheduled task; dashboard = phone "
        "remote-control web UI (needs JARVIS restart); openai_docs / telegram "
        "= MCP servers (need JARVIS restart). "
        "RULES: when the user says the bot link is down, lost, or asks for "
        "bot status, first call ensure/need=errors (it kills stray pollers "
        "and restarts the bridge); for fresh Telegram messages, history, or "
        "channels, ensure/need=telegram; for phone control, QR, or remote "
        "buttons, ensure/need=phone; for documentation lookup, "
        "ensure/need=docs; for the TelegramAI bot, ensure/need=telegramai. "
        "Do NOT ask which component — decide from the goal. If ensure reports "
        "a restart is needed and the user wants it working now (or said "
        "'make it work'), call restart/target=jarvis without asking again. "
        "Direct control: status, enable, disable, start, stop per component."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "status | ensure | enable | disable | start | stop | run | restart",
            },
            "target": {
                "type": "STRING",
                "description": "all | error_bot | telegramai | dashboard | openai_docs | telegram | jarvis",
            },
            "need": {
                "type": "STRING",
                "description": "goal for ensure: telegram | phone | errors | docs | telegramai",
            },
        },
        "required": ["action"],
    },
    "handler": startup_selfmanage,
}
