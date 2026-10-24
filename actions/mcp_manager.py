"""Configure MCP connections for discovery on the next JARVIS launch."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from core.undo import push_undo

try:
    # Подтверждение с экрана: без него модель могла бы сама добавить себе
    # авто-одобренный MCP-сервер (approval=never) и получить выполнение
    # произвольных команд без ведома владельца.
    from core.confirm import pending_title, request as request_confirmation
except Exception:  # headless-режим: confirm не смонтирован — запрещаем вместо тихого пропуска
    request_confirmation = None
    pending_title = None

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,47}$")
_ENV_REF_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")


def _config_path() -> Path:
    try:
        from core.paths import get_config_dir

        return get_config_dir() / "mcp_servers.json"
    except Exception:  # pragma: no cover - fallback for very early import
        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        return base / "config" / "mcp_servers.json"


def _load() -> tuple[dict, str | None]:
    path = _config_path()
    try:
        original = path.read_text(encoding="utf-8")
        data = json.loads(original)
    except FileNotFoundError:
        return {"servers": []}, None
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read MCP configuration: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("servers", []), list):
        raise ValueError("MCP configuration must be a JSON object with a servers list.")
    data.setdefault("servers", [])
    return data, original


def _restore_config(original: str | None) -> str:
    path = _config_path()
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(original, encoding="utf-8")
    return "Restored the previous MCP configuration. Restart JARVIS to apply it."


def _save(data: dict, original: str | None, label: str) -> str:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    push_undo(f"{label} MCP configuration", lambda: _restore_config(original))
    return f"Saved MCP configuration at {path}. Restart JARVIS to load the updated tools."


def _find(servers: list, name: str) -> dict:
    for server in servers:
        if isinstance(server, dict) and server.get("name") == name:
            return server
    raise ValueError(f"MCP server '{name}' is not configured.")


def _parse_env(value: str) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except ValueError as exc:
        raise ValueError("env must be a JSON object mapping environment names to ${VARIABLE} references.") from exc
    if not isinstance(data, dict):
        raise ValueError("env must be a JSON object.")
    for key, item in data.items():
        if not isinstance(key, str) or not isinstance(item, str) or not _ENV_REF_RE.fullmatch(item):
            raise ValueError("Store only ${ENVIRONMENT_VARIABLE} references in env; do not put secrets in this file.")
    return data


def _parse_headers(value: str) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except ValueError as exc:
        raise ValueError("headers must be JSON mapping HTTP header names to ${ENV_NAME} references.") from exc
    if not isinstance(data, dict):
        raise ValueError("headers must be a JSON object.")
    for key, item in data.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                or not isinstance(item, str) or not _ENV_REF_RE.fullmatch(item)):
            raise ValueError("Store only valid HTTP header names and ${ENV_NAME} references; do not put secrets in this file.")
    return data


def mcp_manager(parameters: dict, **_context) -> str:
    p = parameters or {}
    action = str(p.get("action") or "list").strip().lower()
    name = str(p.get("name") or "").strip()
    # The settings panel calls this handler only after a direct human selection.
    # Model initiated policy changes still pass through the confirmation gate.
    user_authorized = _context.get("_user_authorized") is True
    try:
        config, original = _load()
        servers = config["servers"]

        if action == "list":
            if not servers:
                return f"No MCP servers are configured. Use add_http or add_stdio. Config: {_config_path()}"
            rows = []
            for server in servers:
                if not isinstance(server, dict):
                    continue
                transport = server.get("transport", "stdio")
                endpoint = server.get("command")
                if transport == "http":
                    parsed = urlparse(str(server.get("url") or ""))
                    endpoint = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
                rows.append(
                    f"{server.get('name', '?')}: {transport}, "
                    f"{'enabled' if server.get('enabled') else 'disabled'}, "
                    f"approval={server.get('approval', 'always')}, endpoint={endpoint or '?'}"
                )
            return "Configured MCP servers:\n" + "\n".join(rows)

        if action in {"add_http", "add_stdio"}:
            if not _NAME_RE.fullmatch(name):
                return "Server name must start with a letter and contain only letters, digits, underscores, or hyphens (max 48 characters)."
            if any(isinstance(s, dict) and s.get("name") == name for s in servers):
                return f"MCP server '{name}' already exists."
            approval = str(p.get("approval") or "always").lower()
            if approval not in {"always", "writes", "never"}:
                return "approval must be always, writes, or never."
            if action == "add_http":
                url = str(p.get("url") or "").strip()
                parsed = urlparse(url)
                local = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    return "Provide a valid MCP HTTP URL."
                if parsed.username or parsed.password or parsed.fragment:
                    return "Do not put credentials or fragments in the server URL."
                if parsed.query:
                    return "MCP URL query parameters are disabled to prevent credentials leaking into logs. Use a public HTTPS endpoint."
                if parsed.scheme != "https" and not local:
                    return "Remote MCP servers must use HTTPS."
                item = {"name": name, "transport": "http", "url": url}
                headers = _parse_headers(str(p.get("headers") or ""))
                if headers:
                    item["headers"] = headers
            else:
                command = str(p.get("command") or "").strip()
                if not command:
                    return "A local MCP executable is required."
                args = p.get("args") or []
                if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                    return "args must be a list of strings."
                cwd = str(p.get("cwd") or "").strip()
                if cwd:
                    directory = Path(cwd).expanduser().resolve(strict=True)
                    if not directory.is_dir():
                        return "cwd must point to a directory."
                    cwd = str(directory)
                env = _parse_env(str(p.get("env") or ""))
                item = {"name": name, "transport": "stdio", "command": command, "args": args}
                if cwd:
                    item["cwd"] = cwd
                if env:
                    item["env"] = env
            item.update({"enabled": True, "approval": approval})
            if request_confirmation is None:
                return (
                    "The confirmation interface is not available, so I did not "
                    f"add MCP server '{name}'. Nothing was changed."
                )
            if pending_title and pending_title():
                return "Another confirmation is waiting on screen. Resolve it before adding an MCP server."

            def _commit() -> str:
                servers.append(item)
                return _save(config, original, f"added {name}")

            endpoint = item.get("command") or item.get("url")
            return request_confirmation(
                key=f"mcp-add-{name}",
                title=f"Add MCP server: {name}",
                detail=(
                    f"{'stdio' if action == 'add_stdio' else 'http'}: {endpoint}\n"
                    f"approval={approval} — the model will be able to call this "
                    f"server's tools{' WITHOUT asking' if approval == 'never' else ''}.\n"
                    "Restart JARVIS to load it."
                ),
                run=_commit,
            )

        if action in {"enable", "disable", "remove", "set_approval", "set_headers"}:
            if not _NAME_RE.fullmatch(name):
                return "Provide the exact configured server name."
            server = _find(servers, name)
            if action == "enable":
                server["enabled"] = True
            elif action == "disable":
                server["enabled"] = False
            elif action == "remove":
                config["servers"] = [s for s in servers if not (isinstance(s, dict) and s.get("name") == name)]
            else:
                if action == "set_approval":
                    approval = str(p.get("approval") or "").lower()
                    if approval not in {"always", "writes", "never"}:
                        return "approval must be always, writes, or never."
                    if (
                        approval == "never"
                        and request_confirmation is not None
                        and not user_authorized
                    ):
                        if pending_title and pending_title():
                            return "Another confirmation is waiting on screen. Resolve it before changing approval."
                        server["approval"] = "never"  # in-memory only: диск трогает _save в confirm

                        def _commit_approval() -> str:
                            return _save(config, original, f"set_approval {name} on {server['name']}")

                        return request_confirmation(
                            key=f"mcp-approval-never-{name}",
                            title=f"Allow unattended tools: {name}",
                            detail=(
                                f"MCP server '{name}' will be able to run its tools "
                                "WITHOUT asking for confirmation. Restart JARVIS to apply."
                            ),
                            run=_commit_approval,
                        )
                    server["approval"] = approval
                else:
                    if str(server.get("transport") or "stdio").lower() != "http":
                        return "HTTP headers apply only to HTTP MCP servers."
                    headers = _parse_headers(str(p.get("headers") or ""))
                    if headers:
                        server["headers"] = headers
                    else:
                        server.pop("headers", None)
            return _save(config, original, f"{action} {name}")

        return "Use list, add_http, add_stdio, enable, disable, remove, set_approval, or set_headers."
    except (OSError, ValueError) as exc:
        return str(exc)


TOOL = {
    "name": "mcp_manager",
    "description": (
        "Configure external MCP servers for JARVIS. Add HTTP or local stdio servers, "
        "list, enable, disable, remove, change their approval policy, or set HTTP headers "
        "through environment variable references. Config changes "
        "take effect after restarting JARVIS. Never place passwords or API keys directly "
        "in server URLs or config; stdio env values must be ${ENV_NAME} references."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list | add_http | add_stdio | enable | disable | remove | set_approval | set_headers"},
            "name": {"type": "STRING", "description": "Short MCP server name"},
            "transport": {"type": "STRING", "description": "stdio or http (inferred from action)"},
            "url": {"type": "STRING", "description": "HTTPS MCP endpoint for add_http"},
            "command": {"type": "STRING", "description": "Executable for add_stdio"},
            "args": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Arguments for a local stdio server"},
            "cwd": {"type": "STRING", "description": "Optional working directory for stdio"},
            "env": {"type": "STRING", "description": "Optional JSON map of server env names to ${ENV_NAME} references"},
            "approval": {"type": "STRING", "description": "always | writes | never; default always"},
            "headers": {"type": "STRING", "description": "Optional JSON map of HTTP header names to ${ENV_NAME} references; secrets stay in environment variables"},
        },
        "required": ["action"],
    },
    "handler": mcp_manager,
}
