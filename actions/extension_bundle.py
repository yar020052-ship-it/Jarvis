"""Export and install portable JARVIS skill + MCP configuration bundles."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from core.confirm import pending_title, request as request_confirmation

_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ENV_REF = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
_MAX_BUNDLE = 8_000_000
_MAX_FILES = 100


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _mcp_config() -> Path:
    return _base_dir() / "config" / "mcp_servers.json"


def _skill_root() -> Path:
    return _base_dir() / "skills"


def _safe_server(entry: dict) -> dict:
    allowed = {"name", "transport", "url", "command", "args", "cwd", "env", "headers", "approval"}
    item = {key: value for key, value in entry.items() if key in allowed}
    transport = str(item.get("transport") or "stdio").lower()
    if transport not in {"stdio", "http"}:
        raise ValueError(f"MCP server '{item.get('name', '?')}' uses an unsupported transport.")
    item["transport"] = transport
    if "url" in item:
        from urllib.parse import urlparse
        parsed = urlparse(str(item["url"]))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"MCP server '{item.get('name', '?')}' URL must not contain credentials, query parameters, or fragments.")
        local = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or (parsed.scheme != "https" and not local):
            raise ValueError(f"MCP server '{item.get('name', '?')}' needs HTTPS (HTTP is allowed only for localhost).")
    if transport == "stdio" and not str(item.get("command") or "").strip():
        raise ValueError(f"MCP server '{item.get('name', '?')}' has no executable command.")
    if transport == "stdio" and (not isinstance(item.get("args", []), list) or not all(isinstance(arg, str) for arg in item.get("args", []))):
        raise ValueError(f"MCP server '{item.get('name', '?')}' has invalid command arguments.")
    for field in ("env", "headers"):
        values = item.get(field, {})
        if not isinstance(values, dict) or any(not isinstance(v, str) or not _ENV_REF.fullmatch(v) for v in values.values()):
            raise ValueError(f"MCP server '{item.get('name', '?')}' has plaintext or invalid {field}; export stopped to avoid including secrets.")
        if field == "headers" and any(not isinstance(k, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k) for k in values):
            raise ValueError(f"MCP server '{item.get('name', '?')}' has an invalid HTTP header name.")
    item["enabled"] = False
    return item


def _read_bundle(path: Path) -> tuple[dict, dict[str, bytes]]:
    path = path.expanduser().resolve(strict=True)
    if path.suffix.lower() != ".zip" or not path.is_file():
        raise ValueError("Provide the path to a local .zip extension bundle.")
    if path.stat().st_size > _MAX_BUNDLE:
        raise ValueError("Bundle is larger than the 8 MB limit.")
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        if len(infos) > _MAX_FILES:
            raise ValueError("Bundle contains too many files.")
        total = 0
        for info in infos:
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts or "\\" in info.filename:
                raise ValueError("Bundle contains an unsafe file path.")
            if info.is_dir():
                continue
            total += info.file_size
            if total > _MAX_BUNDLE:
                raise ValueError("Unpacked bundle exceeds the 8 MB limit.")
            files[info.filename] = archive.read(info)
    manifest = json.loads(files.get("manifest.json", b"{}").decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != "jarvis-extension-bundle-v1":
        raise ValueError("Unsupported or missing JARVIS bundle manifest.")
    return manifest, files


def _export(skill_name: str, mcp_names: list) -> str:
    if not _SKILL_NAME.fullmatch(skill_name):
        return "Provide the exact skill name (lowercase letters, digits, hyphens, or underscores)."
    source = (_skill_root() / skill_name).resolve(strict=True)
    if _skill_root().resolve() not in source.parents or not source.is_dir():
        return "Skill not found. Use load_skill action=list to see available skills."
    files = {}
    total = 0
    for path in source.rglob("*"):
        if path.is_symlink():
            return "Skill bundles cannot contain symlinks."
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            size = path.stat().st_size
            if size > _MAX_BUNDLE - total:
                return "Skill contents exceed bundle size or file-count limits."
            data = path.read_bytes()
            total += len(data)
            if len(files) >= _MAX_FILES - 4 or total > _MAX_BUNDLE:
                return "Skill contents exceed bundle size or file-count limits."
            files[f"skills/{skill_name}/{relative}"] = data
    if f"skills/{skill_name}/SKILL.md" not in files:
        return "Skill has no SKILL.md manifest."
    selected = set(str(x) for x in (mcp_names or []))
    servers = []
    if selected:
        try:
            config = json.loads(_mcp_config().read_text(encoding="utf-8"))
            configured = config.get("servers", [])
        except FileNotFoundError:
            configured = []
        if not isinstance(configured, list):
            return "MCP configuration has an invalid servers list."
        found = {item.get("name"): item for item in configured if isinstance(item, dict)}
        missing = selected - found.keys()
        if missing:
            return "Unknown MCP server name(s): " + ", ".join(sorted(missing))
        try:
            servers = [_safe_server(found[name]) for name in sorted(selected)]
        except ValueError as exc:
            return str(exc)
    files["mcp_servers.json"] = json.dumps({"servers": servers}, ensure_ascii=False, indent=2).encode("utf-8")
    manifest = {"format": "jarvis-extension-bundle-v1", "skill": skill_name,
                "mcp_servers": [x["name"] for x in servers], "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    destination = _base_dir() / "config" / "extension_bundles"
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = destination / f"{skill_name}-{stamp}.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return f"Created JARVIS extension bundle: {output}\nSkill files: {sum(n.startswith('skills/') for n in files)}; MCP server configs: {len(servers)} (disabled in the bundle)."


def _install_bundle(path_text: str) -> str:
    bundle = Path(path_text).expanduser().resolve(strict=True)
    manifest, files = _read_bundle(bundle)
    skill_name = str(manifest.get("skill") or "")
    if not _SKILL_NAME.fullmatch(skill_name):
        return "Bundle contains an invalid skill name."
    skill_prefix = f"skills/{skill_name}/"
    skill_files = {name[len(skill_prefix):]: data for name, data in files.items() if name.startswith(skill_prefix)}
    if "SKILL.md" not in skill_files or not skill_files:
        return "Bundle does not contain its declared skill."
    mcp_data = json.loads(files.get("mcp_servers.json", b'{"servers":[]}').decode("utf-8"))
    servers = mcp_data.get("servers", []) if isinstance(mcp_data, dict) else None
    if not isinstance(servers, list) or any(not isinstance(s, dict) or not s.get("name") for s in servers):
        return "Bundle contains an invalid MCP server list."
    validated = []
    names = set()
    for server in servers:
        server = dict(server)
        name = str(server.get("name") or "")
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,47}", name) or name in names:
            return "Bundle contains an invalid or duplicate MCP server name."
        names.add(name)
        transport = str(server.get("transport") or "stdio").lower()
        if transport not in {"stdio", "http"}:
            return f"Bundle MCP server '{name}' uses an unsupported transport."
        if transport == "stdio" and (not isinstance(server.get("command"), str) or not server.get("command")):
            return f"Bundle MCP server '{name}' has no executable command."
        if transport == "http":
            from urllib.parse import urlparse
            parsed = urlparse(str(server.get("url") or ""))
            local = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or (parsed.scheme != "https" and not local):
                return f"Bundle MCP server '{name}' needs a valid HTTPS URL (HTTP is allowed only for localhost)."
        server["transport"] = transport
        if server.get("approval", "always") not in {"always", "writes", "never"}:
            return f"Bundle MCP server '{name}' has an invalid approval policy."
        try:
            validated.append(_safe_server(server))
        except ValueError as exc:
            return str(exc)
    servers = validated
    if pending_title():
        return "Another confirmation is waiting on screen. Resolve it before installing this bundle."
    detail = (f"Archive: {bundle}\nSkill: {skill_name} ({len(skill_files)} file(s))\n"
              f"MCP server entries: {len(servers)}; all imported servers remain disabled.\n"
              + "\n".join(f"- {s.get('name')}: {s.get('transport', 'stdio')} — {s.get('command') or s.get('url')}" for s in servers)
              + "\n"
              "The JARVIS skill folder and MCP config will be changed. Restart JARVIS to load them.")
    return request_confirmation(
        key=f"install-extension-{skill_name}",
        title=f"Install JARVIS extension bundle: {skill_name}",
        detail=detail,
        run=lambda: _commit_install(skill_name, skill_files, servers),
    )


def _commit_install(skill_name: str, skill_files: dict[str, bytes], servers: list[dict]) -> str:
    destination = _skill_root() / skill_name
    if destination.exists():
        return f"Skill '{skill_name}' is already installed; refusing to overwrite it."
    for relative in skill_files:
        member = PurePosixPath(relative)
        if member.is_absolute() or ".." in member.parts or "\\" in relative:
            return "Bundle contains an unsafe skill resource path."
    config_path = _mcp_config()
    try:
        original = config_path.read_text(encoding="utf-8")
        config = json.loads(original)
    except FileNotFoundError:
        original = None
        config = {"servers": []}
    except (OSError, ValueError) as exc:
        return f"Could not read current MCP configuration: {exc}"
    if not isinstance(config, dict):
        return "Current MCP configuration must be a JSON object."
    current = config.get("servers", [])
    if not isinstance(current, list):
        return "Current MCP configuration has an invalid servers list."
    existing = {x.get("name") for x in current if isinstance(x, dict)}
    duplicates = [x.get("name") for x in servers if x.get("name") in existing]
    if duplicates:
        return "MCP server name(s) already configured: " + ", ".join(duplicates)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-bundle-", dir=str(_skill_root().parent)))
    staged = temp_dir / skill_name
    destination_created = False
    try:
        for relative, data in skill_files.items():
            target = staged.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        from core.skill_loader import _parse
        skill_record = _parse(staged / "SKILL.md")
        if not skill_record or skill_record.get("name") != skill_name:
            return "Bundle skill manifest is invalid or its declared name does not match the folder."
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, destination)
        destination_created = True
        updated = dict(config)
        updated["servers"] = current + [{**item, "enabled": False} for item in servers]
        temporary = config_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, config_path)
    except Exception as exc:
        if destination_created:
            shutil.rmtree(destination, ignore_errors=True)
        return f"Bundle installation failed and created files were rolled back: {exc}"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return f"Installed skill '{skill_name}' and {len(servers)} MCP server config(s). Servers are disabled. Restart JARVIS to load the skill; review and enable servers in the plugin manager when ready."


def extension_bundle(parameters: dict, **_context) -> str:
    p = parameters or {}
    action = str(p.get("action") or "export").strip().lower()
    if action == "export":
        names = p.get("mcp_names") or []
        if not isinstance(names, list):
            return "mcp_names must be a list of configured server names."
        try:
            return _export(str(p.get("skill_name") or "").strip(), names)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            return f"Could not create extension bundle: {exc}"
    if action == "inspect":
        try:
            manifest, files = _read_bundle(Path(str(p.get("path") or "")))
            return "Bundle manifest:\n" + json.dumps(manifest, ensure_ascii=False, indent=2) + "\nFiles:\n" + "\n".join(sorted(files))
        except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            return f"Could not inspect extension bundle: {exc}"
    if action == "install":
        try:
            return _install_bundle(str(p.get("path") or ""))
        except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            return f"Could not prepare extension bundle: {exc}"
    return "Use action=export, inspect, or install."


TOOL = {
    "name": "extension_bundle",
    "description": (
        "Packages a JARVIS skill and its resource files together with selected MCP server configurations into a local ZIP, inspects a bundle, or installs one after explicit HUD confirmation. "
        "Imported MCP servers remain disabled. Environment-variable references are included, never secret values."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "export | inspect | install"},
            "skill_name": {"type": "STRING", "description": "Exact installed skill name for export"},
            "mcp_names": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Optional MCP server names to include when exporting"},
            "path": {"type": "STRING", "description": "Local bundle .zip path for inspect or install"},
        },
        "required": ["action"],
    },
    "handler": extension_bundle,
}
