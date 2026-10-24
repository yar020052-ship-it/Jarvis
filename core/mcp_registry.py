"""MCP client registry for external JARVIS tools.

Servers are explicitly configured in ``config/mcp_servers.json``. The official
MCP Python SDK is optional at import time; when installed, enabled stdio and
Streamable HTTP servers are discovered at startup and their tools are exposed
through JARVIS function calling.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
import re
import shutil
import threading
import urllib.parse
import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.confirm import pending_title, request_and_wait

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,47}$")
_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_MAX_TOOL_OUTPUT = 18_000
_MAX_TOOL_COUNT = 100
_MAX_RESOURCE_MEDIA = 4_000_000
_MAX_RESOURCE_IMAGES = 2
_SUPPORTED_RESOURCE_MEDIA = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"}


@dataclass
class MCPToolResult:
    text: str
    media: list[dict] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.text} [{len(self.media)} media attachment(s)]" if self.media else self.text


def _plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return value
    return {}


def _tool_name(server: str, tool: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_]", "_", f"mcp_{server}_{tool}")
    raw = re.sub(r"_+", "_", raw).strip("_") or "mcp_tool"
    if raw[0].isdigit():
        raw = "mcp_" + raw
    if len(raw) <= 64:
        return raw
    import hashlib

    suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return raw[:55] + "_" + suffix


def _schema_for_gemini(value: dict, _depth: int = 0) -> dict:
    """Keep the JSON Schema subset accepted by Gemini function declarations."""
    if _depth >= 8:
        return {"type": "STRING", "description": "Nested value omitted (schema depth limit)."}
    if not isinstance(value, dict):
        return {"type": "OBJECT", "properties": {}}
    out = {}
    raw_type = value.get("type", "object")
    if isinstance(raw_type, list):
        non_null = [x for x in raw_type if x != "null"]
        raw_type = non_null[0] if len(non_null) == 1 else "object"
        if "null" in value.get("type", []):
            out["nullable"] = True
    converted = {
        "object": "OBJECT", "string": "STRING", "integer": "INTEGER",
        "number": "NUMBER", "boolean": "BOOLEAN", "array": "ARRAY",
    }.get(str(raw_type).lower(), "STRING")
    out["type"] = converted
    if isinstance(value.get("description"), str):
        out["description"] = value["description"][:1000]
    if isinstance(value.get("enum"), list) and value["enum"]:
        out["enum"] = [str(item) for item in value["enum"][:100]]
    if isinstance(value.get("format"), str):
        out["format"] = value["format"]
    if converted == "OBJECT":
        props = value.get("properties")
        out["properties"] = {
            str(key): _schema_for_gemini(schema, _depth + 1)
            for key, schema in props.items()
            if isinstance(key, str) and isinstance(schema, dict)
        } if isinstance(props, dict) else {}
        required = value.get("required")
        if isinstance(required, list):
            out["required"] = [str(item) for item in required if isinstance(item, str)]
    elif converted == "ARRAY":
        items = value.get("items")
        out["items"] = _schema_for_gemini(items, _depth + 1) if isinstance(items, dict) else {"type": "STRING"}
    return out


def _is_method_not_found(exc: BaseException) -> bool:
    """True if exc (or anything nested in an ExceptionGroup) is MCP 'Method not found'.

    Servers like developers.openai.com/mcp don't implement resources/list and
    answer with JSON-RPC -32601 wrapped by anyio into an ExceptionGroup, so a
    plain str(exc) check on the outer exception never matches.
    """
    seen = set()

    def _walk(err: BaseException) -> bool:
        if id(err) in seen:
            return False
        seen.add(id(err))
        text = f"{type(err).__name__}: {err}".lower()
        if "method not found" in text or "-32601" in text:
            return True
        nested = getattr(err, "exceptions", None)
        if nested:
            return any(_walk(sub) for sub in nested)
        cause = getattr(err, "__cause__", None) or getattr(err, "__context__", None)
        if isinstance(cause, BaseException):
            return _walk(cause)
        return False

    return _walk(exc)


class MCPRegistry:
    def __init__(
        self,
        config_path: Path,
        reserved_names: set[str] | None = None,
        logger: Callable[[str], None] = print,
    ):
        self.config_path = Path(config_path)
        self.reserved_names = set(reserved_names or ())
        self.logger = logger
        self._servers: dict[str, dict] = {}
        self._tools: dict[str, dict] = {}
        self._errors: dict[str, str] = {}
        self._runtime_errors: list[dict[str, str]] = []
        self._ready = threading.Event()
        self._load_config()
        # Discovery spawns server processes + network (HTTP) — seconds.
        # Run it in background so startup/connect isn't blocked; readers below
        # wait for readiness (bounded), and per-connect config rebuilds pick up
        # late tools automatically.
        threading.Thread(target=self._discover_bg, daemon=True, name="mcp-discover").start()

    def _discover_bg(self) -> None:
        try:
            self._discover()
        except Exception as exc:
            self._errors["runtime"] = f"MCP discovery failed: {exc}"
        finally:
            self._ready.set()

    def _wait_ready(self, timeout: float = 60.0) -> None:
        try:
            self._ready.wait(timeout=timeout)
        except Exception:
            pass

    def _log(self, message: str) -> None:
        try:
            self.logger(message)
        except Exception:
            pass

    def _load_config(self) -> None:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._log(f"MCP: no config at {self.config_path}; no MCP servers enabled.")
            return
        except (OSError, ValueError) as exc:
            self._log(f"MCP: could not read config: {exc}")
            return
        items = data.get("servers", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            self._log("MCP: config field 'servers' must be a list.")
            return
        for entry in items:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not _NAME_RE.fullmatch(name):
                self._log(f"MCP: invalid server name {name!r}; skipped.")
                continue
            if not entry.get("enabled", False):
                continue
            if name in self._servers:
                self._log(f"MCP: duplicate server '{name}'; later entry skipped.")
                continue
            self._servers[name] = entry
            if len(self._servers) >= 8:
                self._log("MCP: only the first 8 enabled servers are loaded.")
                break

    def _server_params(self, entry: dict):
        transport = str(entry.get("transport") or "stdio").lower()
        if transport == "http":
            url = str(entry.get("url") or "").strip()
            parsed = urllib.parse.urlparse(url)
            local_hosts = {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("HTTP MCP server requires a valid http(s) URL.")
            if parsed.scheme != "https" and parsed.hostname.lower() not in local_hosts:
                raise ValueError("Remote MCP servers must use HTTPS.")
            return url
        if transport != "stdio":
            raise ValueError("Supported MCP transports are 'stdio' and 'http'.")
        command = str(entry.get("command") or "").strip()
        if not command:
            raise ValueError("stdio MCP server requires a command.")
        if not (Path(command).is_file() or shutil.which(command)):
            raise ValueError(f"MCP server executable was not found: {command}")
        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValueError("MCP server 'args' must be a list of strings.")
        cwd = str(entry.get("cwd") or "").strip() or None
        if cwd:
            cwd_path = Path(cwd).expanduser().resolve(strict=True)
            if not cwd_path.is_dir():
                raise ValueError("MCP server cwd must be a directory.")
            cwd = str(cwd_path)
        env = {}
        raw_env = entry.get("env", {})
        if not isinstance(raw_env, dict):
            raise ValueError("MCP server 'env' must be an object.")
        for key, value in raw_env.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("MCP server environment keys and values must be strings.")
            match = _ENV_REF_RE.fullmatch(value)
            if match:
                resolved = os.environ.get(match.group(1))
                if resolved is None:
                    raise ValueError(f"Required environment variable {match.group(1)} is not set.")
                env[key] = resolved
            else:
                env[key] = value
        from mcp import StdioServerParameters

        return StdioServerParameters(command=command, args=args, env=env or None, cwd=cwd)

    @staticmethod
    def _http_headers(entry: dict) -> dict[str, str]:
        headers = entry.get("headers", {})
        if not isinstance(headers, dict):
            raise ValueError("HTTP MCP 'headers' must be an object of environment references.")
        resolved = {}
        for key, value in headers.items():
            if not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key):
                raise ValueError("HTTP MCP header names must be valid HTTP tokens.")
            match = _ENV_REF_RE.fullmatch(str(value))
            if not match:
                raise ValueError(f"Header '{key}' must use a ${{ENVIRONMENT_VARIABLE}} reference.")
            secret = os.environ.get(match.group(1))
            if secret is None:
                raise ValueError(f"Required environment variable {match.group(1)} is not set.")
            resolved[key] = secret
        return resolved

    @asynccontextmanager
    async def _client(self, entry: dict):
        from mcp import Client

        if str(entry.get("transport") or "stdio").lower() == "http" and entry.get("headers"):
            headers = self._http_headers(entry)
            import httpx
            from mcp.client.streamable_http import streamable_http_client

            timeout = httpx.Timeout(connect=30, read=300, write=30, pool=30)
            async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
                transport = streamable_http_client(
                    str(entry.get("url") or ""), http_client=http_client
                )
                async with Client(transport) as client:
                    yield client
            return
        async with Client(self._server_params(entry)) as client:
            yield client

    @staticmethod
    def _safe_error(entry: dict, exc: Exception) -> str:
        message = str(exc)
        url = str(entry.get("url") or "")
        if url:
            parsed = urllib.parse.urlparse(url)
            safe_url = urllib.parse.urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
            )
            message = message.replace(url, safe_url)
        raw_env = entry.get("env", {})
        for mapping in (raw_env, entry.get("headers", {})):
            if not isinstance(mapping, dict):
                continue
            for ref in mapping.values():
                match = _ENV_REF_RE.fullmatch(str(ref))
                secret = os.environ.get(match.group(1)) if match else None
                if secret:
                    message = message.replace(secret, "[redacted]")
        return f"{type(exc).__name__}: {message}"[:1000]

    async def _list_remote(self, entry: dict) -> list:
        async with self._client(entry) as client:
            found = []
            cursor = None
            while True:
                page = await asyncio.wait_for(client.list_tools(cursor=cursor), timeout=25)
                found.extend(page.tools)
                cursor = getattr(page, "next_cursor", None)
                if not cursor:
                    break
            return found

    def _discover(self) -> None:
        if not self._servers:
            return
        try:
            import mcp  # noqa: F401
        except ImportError:
            self._errors["runtime"] = "Install the 'mcp' Python package and restart JARVIS."
            self._log("MCP: SDK is not installed; configured servers were not loaded.")
            return
        for server_name, entry in self._servers.items():
            try:
                raw_allowed_tools = entry.get("allowed_tools")
                if raw_allowed_tools is not None:
                    if not isinstance(raw_allowed_tools, list) or any(
                        not isinstance(name, str) or not name.strip()
                        for name in raw_allowed_tools
                    ):
                        raise ValueError("MCP 'allowed_tools' must be a list of non-empty tool names.")
                    allowed_tools = set(raw_allowed_tools)
                else:
                    allowed_tools = None

                raw_tool_schemas = entry.get("tool_schemas", {})
                if raw_tool_schemas is None:
                    raw_tool_schemas = {}
                if not isinstance(raw_tool_schemas, dict) or any(
                    not isinstance(name, str) or not name.strip() or not isinstance(schema, dict)
                    for name, schema in raw_tool_schemas.items()
                ):
                    raise ValueError("MCP 'tool_schemas' must map tool names to JSON Schema objects.")
                if allowed_tools is not None and any(
                    name not in allowed_tools for name in raw_tool_schemas
                ):
                    raise ValueError("MCP 'tool_schemas' may only define tools listed in 'allowed_tools'.")

                tools = asyncio.run(self._list_remote(entry))
                exposed_count = 0
                for remote in tools:
                    if len(self._tools) >= _MAX_TOOL_COUNT:
                        self._log(f"MCP: the {_MAX_TOOL_COUNT}-tool limit was reached; remaining tools skipped.")
                        break
                    remote_name = str(getattr(remote, "name", "")).strip()
                    if not remote_name:
                        continue
                    if allowed_tools is not None and remote_name not in allowed_tools:
                        continue
                    name = _tool_name(server_name, remote_name)
                    if name in self.reserved_names or name in self._tools:
                        self._log(f"MCP: tool name collision '{name}'; skipped.")
                        continue
                    description = str(getattr(remote, "description", "") or "")
                    annotations = _plain(getattr(remote, "annotations", None))
                    read_only = bool(
                        annotations.get("readOnlyHint", annotations.get("read_only_hint", False))
                    )
                    approval = str(entry.get("approval") or "always").lower()
                    if approval not in {"always", "writes", "never"}:
                        approval = "always"
                    self._tools[name] = {
                        "server": server_name,
                        "remote_name": remote_name,
                        "description": description[:3000],
                        "schema": _schema_for_gemini(
                            raw_tool_schemas.get(remote_name)
                            or getattr(remote, "input_schema", {})
                            or {}
                        ),
                        "read_only": read_only,
                        "approval": approval,
                    }
                    exposed_count += 1
                self._log(f"MCP: discovered {exposed_count} tool(s) from '{server_name}'.")
                # An explicit tool allowlist is a capability boundary: do not
                # add the implicit list_resources/read_resource helpers.
                if allowed_tools is None:
                    self._discover_resource_tools(server_name, entry)
            except Exception as exc:
                safe_error = self._safe_error(entry, exc)
                self._errors[server_name] = safe_error
                self._log(f"MCP: could not connect to '{server_name}': {safe_error}")

    def _discover_resource_tools(self, server_name: str, entry: dict) -> None:
        try:
            resources, templates = asyncio.run(self._list_resources(entry))
        except Exception as exc:
            # Не фатально: инструменты сервера уже подключены выше.
            # Внешние серверы (напр. OpenAI_Docs) иногда не отдают resources/list.
            if _is_method_not_found(exc):
                # Сервер просто не реализует resources/list — это норма, а не ошибка.
                return
            self._log(f"MCP: '{server_name}' без resources (инструменты работают): {self._safe_error(entry, exc)}")
            return
        if not resources and not templates:
            return
        approval = str(entry.get("approval") or "always").lower()
        if approval not in {"always", "writes", "never"}:
            approval = "always"
        for suffix, kind, description, schema in (
            ("list_resources", "resource_list", "Lists text documents and media resources exposed by this MCP server. Call this before reading a resource when its URI is unknown.", {"type": "OBJECT", "properties": {}}),
            ("read_resource", "resource_read", "Reads an MCP resource by URI. Text is returned as text; supported image and PDF contents are attached directly to JARVIS for analysis.", {"type": "OBJECT", "properties": {"uri": {"type": "STRING", "description": "Exact URI returned by the server resource list"}}, "required": ["uri"]}),
        ):
            name = _tool_name(server_name, suffix)
            if name in self.reserved_names or name in self._tools:
                self._log(f"MCP: generated resource tool name collision '{name}'; skipped.")
                continue
            if len(self._tools) >= _MAX_TOOL_COUNT:
                break
            self._tools[name] = {
                "server": server_name, "remote_name": suffix, "kind": kind,
                "description": description, "schema": schema,
                "read_only": True, "approval": approval,
            }
        self._log(f"MCP: enabled resource listing for '{server_name}' ({len(resources)} resource(s), {len(templates)} template(s)).")

    async def _list_resources(self, entry: dict) -> tuple[list, list]:
        resources, templates = [], []
        async with self._client(entry) as client:
            cursor = None
            for _ in range(10):
                page = await asyncio.wait_for(client.list_resources(cursor=cursor), timeout=25)
                resources.extend(getattr(page, "resources", []) or [])
                cursor = getattr(page, "next_cursor", None)
                if not cursor or len(resources) >= 200:
                    break
            cursor = None
            try:
                for _ in range(10):
                    page = await asyncio.wait_for(client.list_resource_templates(cursor=cursor), timeout=25)
                    templates.extend(getattr(page, "resource_templates", []) or [])
                    cursor = getattr(page, "next_cursor", None)
                    if not cursor or len(templates) >= 200:
                        break
            except Exception:
                templates = []
        return resources[:200], templates[:200]

    def get_tool_declarations(self) -> list[dict]:
        self._wait_ready()
        declarations = []
        for name, tool in self._tools.items():
            policy = tool["approval"]
            warning = "" if tool["read_only"] else " This tool may change data or trigger external effects."
            description = (
                f"MCP server '{tool['server']}' tool '{tool['remote_name']}': "
                f"{tool['description']}{warning} Approval policy: {policy}."
            )
            declarations.append({
                "name": name,
                "description": description[:4000],
                "parameters": tool["schema"],
            })
        return declarations

    def has(self, name: str) -> bool:
        self._wait_ready()
        return name in self._tools

    def list_for_ui(self) -> list[dict]:
        return [
            {"name": name, "server": item["server"], "remote_tool": item["remote_name"],
             "read_only": item["read_only"], "approval": item["approval"]}
            for name, item in self._tools.items()
        ]

    def list_configured_servers(self) -> list[dict]:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            rows = data.get("servers", []) if isinstance(data, dict) else []
        except (OSError, ValueError):
            return []
        out = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            transport = str(item.get("transport") or "stdio").lower()
            tools = [x for x, rec in self._tools.items() if rec.get("server") == name]
            endpoint = str(item.get("command") or "") if transport == "stdio" else str(item.get("url") or "")
            if transport == "http":
                parsed = urllib.parse.urlparse(endpoint)
                endpoint = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
            out.append({
                "name": name, "enabled": item.get("enabled") is True,
                "transport": transport, "approval": item.get("approval", "always"),
                "tool_count": len(tools), "valid": True, "kind": "mcp",
                "description": f"{transport} · {len(tools)} loaded tool(s) · approval={item.get('approval', 'always')} · {endpoint or 'no endpoint'}",
            })
        return out

    def set_server_approval(self, server_name: str, approval: str) -> bool:
        """Apply a saved server policy to already-discovered tools immediately."""
        self._wait_ready()
        policy = str(approval or "").lower()
        if policy not in {"always", "writes", "never"}:
            return False
        entry = self._servers.get(server_name)
        if not entry:
            return False
        entry["approval"] = policy
        for tool in self._tools.values():
            if tool.get("server") == server_name:
                tool["approval"] = policy
        return True

    def status(self) -> str:
        lines = [f"MCP configuration: {self.config_path}"]
        if not self._ready.is_set():
            lines.append("Discovery still running in background…")
        if not self._servers:
            lines.append("No enabled MCP servers are configured.")
        for name in self._servers:
            tools = [key for key, value in self._tools.items() if value["server"] == name]
            lines.append(f"{name}: {len(tools)} tool(s)" if tools else f"{name}: unavailable or no tools")
            if name in self._errors:
                lines.append(f"  Error: {self._errors[name]}")
        if "runtime" in self._errors:
            lines.append(self._errors["runtime"])
        if self._runtime_errors:
            lines.append(f"Runtime MCP call errors this session: {len(self._runtime_errors)}.")
            for item in self._runtime_errors[-5:]:
                lines.append(
                    f"  {item.get('server', '?')}.{item.get('tool', '?')}: "
                    f"{item.get('error', 'unknown error')}"
                )
        return "\n".join(lines)

    def _remember_runtime_error(self, tool: dict, error: str) -> None:
        self._runtime_errors.append({
            "server": str(tool.get("server") or "?"),
            "tool": str(tool.get("remote_name") or "?"),
            "error": str(error)[:500],
        })
        del self._runtime_errors[:-20]

    async def _call_remote(
        self,
        entry: dict,
        tool: dict,
        arguments: dict,
        output_limit: int = _MAX_TOOL_OUTPUT,
    ) -> str | MCPToolResult:
        kind = tool.get("kind", "tool")
        if kind == "resource_list":
            resources, templates = await self._list_resources(entry)
            rows = []
            for item in resources:
                rows.append({"name": getattr(item, "name", ""), "uri": getattr(item, "uri", ""),
                             "description": getattr(item, "description", ""), "mimeType": getattr(item, "mime_type", "")})
            for item in templates:
                rows.append({"name": getattr(item, "name", ""), "uriTemplate": getattr(item, "uri_template", ""),
                             "description": getattr(item, "description", ""), "mimeType": getattr(item, "mime_type", "")})
            return "MCP resources:\n" + json.dumps(rows[:200], ensure_ascii=False, default=str)[:_MAX_TOOL_OUTPUT]
        async with self._client(entry) as client:
            if kind == "resource_read":
                result = await asyncio.wait_for(client.read_resource(str(arguments.get("uri") or "")), timeout=90)
                return self._format_resource(result)
            result = await asyncio.wait_for(
                client.call_tool(tool["remote_name"], arguments), timeout=150
            )
        chunks = []
        media = []
        structured = getattr(result, "structured_content", None)
        text_chunks = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                text_chunks.append(text)
            else:
                kind = getattr(block, "type", "content")
                mime = getattr(block, "mime_type", "")
                item = self._media_from_block(block, mime)
                if (item and len(media) < _MAX_RESOURCE_IMAGES
                        and sum(len(x["data"]) for x in media) + len(item["data"]) <= _MAX_RESOURCE_MEDIA):
                    media.append(item)
                    text_chunks.append(f"[Attached {item['mime_type']} from MCP tool result]")
                else:
                    text_chunks.append(f"[{kind}{': ' + str(mime) if mime else ''}]")
        # Some local MCP servers (including OpenClaw Companion) return images
        # inside structured JSON as data URLs rather than ImageContent blocks.
        # Convert those to Gemini media attachments so the model receives pixels
        # instead of a large, unusable base64 string.
        if isinstance(structured, dict):
            structured, _ = self._extract_embedded_image(structured, media)
        cleaned_text_chunks = []
        max_embedded_json = _MAX_RESOURCE_MEDIA * 4 // 3 + 4096
        for text in text_chunks:
            if (len(text) <= max_embedded_json
                    and ("data:image/" in text or '"base64"' in text)):
                try:
                    parsed = json.loads(text)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    parsed, _ = self._extract_embedded_image(parsed, media)
                    text = json.dumps(parsed, ensure_ascii=False, default=str)
            cleaned_text_chunks.append(text[:output_limit])
        text_chunks = cleaned_text_chunks
        # MCP SDKs often expose a string tool result twice: as
        # structuredContent={"result": "..."} and as a text content block.
        # Keep the human-readable text once so callers that parse JSON (such as
        # the Telegram startup briefing) receive valid JSON instead of two
        # concatenated representations of the same value.
        structured_result = (
            structured.get("result")
            if isinstance(structured, dict) and set(structured) == {"result"}
            else None
        )
        text_blob = "\n".join(text_chunks).strip()
        duplicate_structured = (
            isinstance(structured_result, str)
            and text_blob
            and structured_result.strip() == text_blob
        )
        if not duplicate_structured and structured is not None and text_blob:
            try:
                duplicate_structured = json.loads(text_blob) == structured
            except (TypeError, ValueError):
                pass
        if not duplicate_structured and structured is not None:
            chunks.append(json.dumps(structured, ensure_ascii=False, default=str))
        chunks.extend(text_chunks)
        output = "\n".join(chunks).strip() or "MCP tool returned no textual content."
        if getattr(result, "is_error", False):
            output = "MCP tool reported an error:\n" + output
        if media:
            return MCPToolResult(output[:output_limit], media)
        return output[:output_limit]

    @classmethod
    def _extract_embedded_image(cls, value: dict, media: list[dict]) -> tuple[dict, bool]:
        """Sanitize and attach a common `{image: data-url, base64, format}` result."""
        cleaned = dict(value)
        image_value = cleaned.get("image")
        item = cls._media_from_data_url(image_value, "MCP image")
        if item is None:
            encoded = cleaned.get("base64")
            image_format = str(cleaned.get("format") or "").lower()
            mime = {"png": "image/png", "jpeg": "image/jpeg"}.get(image_format)
            item = cls._media_from_base64(encoded, mime, "MCP image") if mime else None
        if item is None:
            return cleaned, False

        already_attached = any(existing.get("data") == item["data"] for existing in media)
        total = sum(len(existing.get("data", b"")) for existing in media)
        attached = already_attached or (
            len(media) < _MAX_RESOURCE_IMAGES
            and total + len(item["data"]) <= _MAX_RESOURCE_MEDIA
        )
        cleaned.pop("base64", None)
        cleaned["image"] = f"[{'attached' if attached else 'omitted'} {item['mime_type']} image]"
        if attached and not already_attached:
            media.append(item)
        return cleaned, attached

    @classmethod
    def _media_from_data_url(cls, value, name: str) -> dict | None:
        if not isinstance(value, str):
            return None
        match = re.fullmatch(r"data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=_-]+)", value.strip())
        if not match:
            return None
        return cls._media_from_base64(match.group(2), match.group(1), name)

    @staticmethod
    def _media_from_base64(value, mime: str | None, name: str) -> dict | None:
        mime = str(mime or "").lower().split(";", 1)[0].strip()
        if mime not in _SUPPORTED_RESOURCE_MEDIA or not isinstance(value, str):
            return None
        if len(value) > (_MAX_RESOURCE_MEDIA * 4 // 3 + 8):
            return None
        try:
            data = base64.b64decode(value, validate=True)
            if not data or len(data) > _MAX_RESOURCE_MEDIA:
                return None
            return {"data": data, "mime_type": mime, "name": str(name or "MCP image")[:100]}
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _media_from_block(block, mime: str) -> dict | None:
        mime = str(mime or "").lower().split(";", 1)[0].strip()
        if mime not in _SUPPORTED_RESOURCE_MEDIA:
            return None
        data = getattr(block, "data", None)
        try:
            if isinstance(data, str):
                data = base64.b64decode(data, validate=True)
            if not isinstance(data, bytes) or not data or len(data) > _MAX_RESOURCE_MEDIA:
                return None
            return {"data": data, "mime_type": mime, "name": str(getattr(block, "name", "") or "MCP attachment")[:100]}
        except (ValueError, TypeError):
            return None

    @classmethod
    def _format_resource(cls, result) -> str | MCPToolResult:
        chunks, media = [], []
        total_media = 0
        for content in getattr(result, "contents", []) or []:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                chunks.append(text[:_MAX_TOOL_OUTPUT])
                continue
            mime = str(getattr(content, "mime_type", "") or "application/octet-stream").lower().split(";", 1)[0]
            blob = getattr(content, "blob", None)
            if isinstance(blob, str):
                if len(blob) > (_MAX_RESOURCE_MEDIA * 4 // 3 + 8):
                    chunks.append(f"[MCP resource binary content too large to attach: {mime}]")
                    continue
                try:
                    blob = base64.b64decode(blob, validate=True)
                except (ValueError, TypeError):
                    blob = None
            if (isinstance(blob, bytes) and blob and mime in _SUPPORTED_RESOURCE_MEDIA
                    and len(media) < _MAX_RESOURCE_IMAGES
                    and total_media + len(blob) <= _MAX_RESOURCE_MEDIA):
                media.append({"data": blob, "mime_type": mime, "name": "MCP resource"})
                total_media += len(blob)
                chunks.append(f"[Attached {mime} MCP resource to the model]")
            elif isinstance(blob, bytes):
                chunks.append(f"[MCP resource binary content not attached: {mime}, {len(blob)} bytes]")
        output = "\n".join(chunks).strip() or "MCP resource returned no content."
        if media:
            return MCPToolResult(output[:_MAX_TOOL_OUTPUT], media)
        return output[:_MAX_TOOL_OUTPUT]

    def call(
        self,
        name: str,
        arguments: dict,
        *,
        _output_limit: int | None = None,
    ) -> str | MCPToolResult:
        self._wait_ready()
        tool = self._tools.get(name)
        if not tool:
            return f"MCP tool '{name}' is not available."
        entry = self._servers.get(tool["server"])
        if not entry:
            return f"MCP server '{tool['server']}' is not enabled."

        output_limit = _MAX_TOOL_OUTPUT
        if _output_limit is not None:
            try:
                output_limit = min(512_000, max(_MAX_TOOL_OUTPUT, int(_output_limit)))
            except (TypeError, ValueError):
                pass

        def _perform() -> str | MCPToolResult:
            result = asyncio.run(
                self._call_remote(
                    entry, tool, arguments or {}, output_limit=output_limit
                )
            )
            output = result.text if isinstance(result, MCPToolResult) else str(result)
            if output.startswith("MCP tool reported an error:"):
                self._remember_runtime_error(tool, output)
            return result

        needs_approval = tool["approval"] == "always" or (
            tool["approval"] == "writes" and not tool["read_only"]
        )
        if not needs_approval:
            try:
                return _perform()
            except Exception as exc:
                error = self._safe_error(entry, exc)
                self._remember_runtime_error(tool, error)
                return f"MCP call failed: {error}"
        if pending_title():
            return "Another confirmation is already waiting on screen. Resolve it before using another MCP tool."
        detail = json.dumps(arguments or {}, ensure_ascii=False, indent=2, default=str)[:4000]
        def _approved_call() -> str:
            try:
                return _perform()
            except Exception as exc:
                error = self._safe_error(entry, exc)
                self._remember_runtime_error(tool, error)
                return f"MCP call failed: {error}"

        return request_and_wait(
            key=f"mcp-{tool['server']}-{tool['remote_name']}",
            title=f"Allow MCP tool {tool['server']}.{tool['remote_name']}?",
            detail=f"Arguments:\n{detail}",
            run=_approved_call,
            wait_timeout=200,
        )
