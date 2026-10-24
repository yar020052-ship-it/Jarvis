"""Repository-aware project operations inspired by Codex's local workflow.

All file paths are constrained to a user-selected project root. Read-only
inspection is immediate, text replacements are undoable, and every command
requires confirmation from the JARVIS interface and runs without a shell.
"""

from __future__ import annotations

import json
import hashlib
import base64
import difflib
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from core.confirm import pending_title, request as request_confirmation
from core.undo import push_undo

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


_WORKSPACE_FILE = _base_dir() / "config" / "workspace.json"
_MAX_FILE_BYTES = 1_000_000
_MAX_OUTPUT_CHARS = 18_000
_MAX_PATCH_FILES = 10
_MAX_PATCH_BYTES = 2_000_000
_MAX_PATCH_PREVIEW = 12_000
_MAX_SAVED_PATCHES = 20
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "target"}


def _stored_workspace() -> str:
    try:
        data = json.loads(_WORKSPACE_FILE.read_text(encoding="utf-8"))
        return str(data.get("path") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def _resolve_workspace(value: str = "") -> Path:
    raw = str(value or _stored_workspace()).strip().strip('"')
    if not raw:
        raise ValueError("No project workspace is selected. Set one with action='set_workspace'.")
    root = Path(raw).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Workspace is not a directory: {root}")
    return root


def _target(root: Path, value: str, *, allow_missing: bool = False) -> Path:
    if not value:
        raise ValueError("A project-relative path is required.")
    raw = Path(value).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve(strict=not allow_missing)
    if resolved != root and root not in resolved.parents:
        raise ValueError("The requested path is outside the selected workspace.")
    return resolved


def _git(root: Path, *args: str, timeout: int = 15) -> str:
    git = shutil.which("git")
    if not git:
        return "Git is not installed or not available on PATH."
    result = subprocess.run(
        [git, "-C", str(root), *args],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    output = (result.stdout or "") + (result.stderr or "")
    return output.strip() or ("(no changes)" if result.returncode == 0 else f"git exited {result.returncode}")


def _inspect(root: Path) -> str:
    entries = []
    try:
        for item in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if item.name in _SKIP_DIRS or item.name.startswith("."):
                continue
            suffix = "/" if item.is_dir() else ""
            entries.append(item.name + suffix)
            if len(entries) >= 80:
                entries.append("… (truncated)")
                break
    except OSError as exc:
        return f"Could not list workspace: {exc}"

    parts = [f"Workspace: {root}", "Top-level files:", "\n".join(entries) or "(empty)"]
    guidance = root / "AGENTS.md"
    if guidance.is_file():
        try:
            text = guidance.read_text(encoding="utf-8")[:12_000]
            parts.extend(["Project instructions from AGENTS.md:", text])
        except (OSError, UnicodeError) as exc:
            parts.append(f"Could not read AGENTS.md: {exc}")
    status = _git(root, "status", "--short", "--branch")
    if "not a git repository" not in status.lower():
        parts.extend(["Git status:", status[:5000]])
    return "\n\n".join(parts)


def _applicable_guidance(root: Path, target: Path) -> str:
    try:
        relative_parent = target.parent.relative_to(root)
    except ValueError:
        return ""
    folders = [root]
    current = root
    for part in relative_parent.parts:
        current = current / part
        folders.append(current)
    sections = []
    for folder in folders:
        manifest = folder / "AGENTS.md"
        if not manifest.is_file():
            continue
        try:
            text = manifest.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        sections.append(f"--- {manifest.relative_to(root).as_posix()} ---\n{text[:12_000]}")
    return "\n\n".join(sections)


def _search(root: Path, query: str) -> str:
    if not query:
        return "A search query is required."
    rg = shutil.which("rg") or shutil.which("rg.exe")
    if rg:
        result = subprocess.run(
            [rg, "--line-number", "--hidden", "--fixed-strings", "--no-heading",
             "--glob", "!.git/**", "--glob", "!.venv/**", "--glob", "!venv/**",
             "--glob", "!node_modules/**", "--", query, str(root)],
            cwd=str(root), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        result_text = (result.stdout or "").strip()
        if result.returncode == 1 and not result_text:
            return "No matches found."
        if result.returncode not in (0, 1):
            return f"Search failed: {(result.stderr or '').strip()}"
        return result_text[:_MAX_OUTPUT_CHARS] or "No matches found."

    matches = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if query.casefold() in line.casefold():
                    matches.append(f"{path.relative_to(root)}:{number}:{line[:300]}")
                    if len(matches) >= 100:
                        return "\n".join(matches) + "\n… (truncated)"
        except (OSError, UnicodeError):
            continue
    return "\n".join(matches) if matches else "No matches found."


def _read(root: Path, relative: str) -> str:
    path = _target(root, relative)
    if not path.is_file():
        return f"Not a file: {path}"
    if path.stat().st_size > _MAX_FILE_BYTES:
        return "File is larger than the 1 MB text-reading limit."
    try:
        guidance = _applicable_guidance(root, path)
        content = path.read_text(encoding="utf-8")
        prefix = f"Applicable AGENTS.md project guidance:\n{guidance}\n\n" if guidance else ""
        return f"{prefix}File: {path.relative_to(root)}\n\n{content}"[:_MAX_OUTPUT_CHARS]
    except UnicodeError:
        return "This file is not valid UTF-8 text; it was not opened as text."


def _restore(path: Path, old_text: str | None) -> str:
    if old_text is None:
        path.unlink(missing_ok=True)
        return f"Removed created file {path.name}."
    path.write_text(old_text, encoding="utf-8", newline="")
    return f"Restored {path.name}."


def _replace(root: Path, relative: str, old_text: str, new_text: str) -> str:
    if not old_text:
        return "old_text must not be empty; use a unique existing text fragment."
    path = _target(root, relative, allow_missing=True)
    existed = path.exists()
    if existed and (not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES):
        return "Target must be a regular UTF-8 text file no larger than 1 MB."
    try:
        before = path.read_text(encoding="utf-8") if existed else ""
    except UnicodeError:
        return "Target is not a UTF-8 text file; no change was made."
    count = before.count(old_text)
    if count != 1:
        return f"Expected exactly one match in {relative}, found {count}; no change was made."
    after = before.replace(old_text, new_text, 1)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(after, encoding="utf-8", newline="")
    except OSError as exc:
        return f"Could not update file: {exc}"
    push_undo(f"edited {path.name} in project", lambda: _restore(path, before if existed else None))
    return f"Updated {path.relative_to(root)}. The edit can be reversed with undo."


def _run_command(root: Path, command: str) -> str:
    try:
        argv = _split_command(command)
    except (ValueError, OSError) as exc:
        return f"Command could not be parsed: {exc}"
    if not argv:
        return "No command was provided."
    try:
        result = subprocess.run(
            argv, cwd=str(root), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return f"Command timed out after 120 seconds.\n{(stdout + stderr)[-_MAX_OUTPUT_CHARS:]}"
    except OSError as exc:
        return f"Could not start command: {exc}"
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return f"Exit code: {result.returncode}\n{output[-_MAX_OUTPUT_CHARS:] or '(no output)'}"


def _split_command(command: str) -> list[str]:
    try:
        if os.name == "nt":
            import ctypes

            argc = ctypes.c_int()
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32
            shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
            shell32.CommandLineToArgvW.argtypes = [
                ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)
            ]
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            native_argv = shell32.CommandLineToArgvW(
                command, ctypes.byref(argc)
            )
            if not native_argv:
                raise ValueError("Windows could not parse the command line.")
            try:
                argv = [native_argv[i] for i in range(argc.value)]
            finally:
                kernel32.LocalFree(native_argv)
        else:
            argv = shlex.split(command, posix=True)
    except (ValueError, OSError) as exc:
        raise ValueError(str(exc)) from exc
    return argv


def _task_dir(root: Path) -> Path:
    identity = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return _base_dir() / "config" / "project_tasks" / identity


def _task_file(root: Path) -> Path:
    return _task_dir(root) / "tasks.json"


def _patch_dir(root: Path) -> Path:
    return _task_dir(root) / "patches"


def _patch_path(root: Path, patch_id: str) -> Path:
    if len(patch_id) != 10 or any(c not in "0123456789abcdef" for c in patch_id.lower()):
        raise ValueError("Invalid patch ID.")
    return _patch_dir(root) / f"{patch_id.lower()}.json"


def _save_patch(root: Path, record: dict) -> None:
    path = _patch_path(root, str(record.get("id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _load_patch(root: Path, patch_id: str) -> dict:
    path = _patch_path(root, patch_id)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("id") != patch_id.lower():
        raise ValueError("Saved patch record is invalid.")
    return value


def _patch_relative_path(root: Path, value: str) -> tuple[Path, str]:
    relative = Path(str(value).strip())
    if not str(value).strip() or relative.is_absolute():
        raise ValueError("Patch paths must be project-relative.")
    path = _target(root, str(relative), allow_missing=True)
    # Reject symlinks in the spelled path, even when they resolve back inside root.
    cursor = root
    for part in relative.parts:
        if part in ("", "."):
            continue
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"Patch target traverses a symbolic link: {value}")
    return path, path.relative_to(root).as_posix()


def _decode_blob(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _write_bytes(path: Path, data: bytes | None) -> None:
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _render_patch(changes: list[dict]) -> str:
    sections = []
    for item in changes:
        old_bytes = _decode_blob(item["before_b64"]) if item["existed"] else b""
        new_bytes = _decode_blob(item["after_b64"])
        old = old_bytes.decode("utf-8").replace("\r\n", "\n").splitlines(keepends=True)
        new = new_bytes.decode("utf-8").replace("\r\n", "\n").splitlines(keepends=True)
        sections.extend(difflib.unified_diff(
            old, new,
            fromfile=item["path"] if item["existed"] else "/dev/null",
            tofile=item["path"],
        ))
    return "".join(sections).rstrip()


def _stage_patch(root: Path, raw_changes) -> str:
    if not isinstance(raw_changes, list) or not 1 <= len(raw_changes) <= _MAX_PATCH_FILES:
        return f"changes must be a list with 1–{_MAX_PATCH_FILES} file entries. No project files were changed."
    prepared = []
    seen = set()
    total = 0
    for entry in raw_changes:
        if not isinstance(entry, dict) or not isinstance(entry.get("content"), str):
            return "Each changes entry needs path and complete UTF-8 content. No project files were changed."
        try:
            target, relative = _patch_relative_path(root, str(entry.get("path") or ""))
            if relative in seen:
                return f"Duplicate path {relative}. No project files were changed."
            seen.add(relative)
            existed = target.exists()
            if existed and (not target.is_file() or target.stat().st_size > _MAX_FILE_BYTES):
                return f"{relative} is not a regular text file within the 1 MB limit. No project files were changed."
            before = target.read_bytes() if existed else b""
            before.decode("utf-8")
            content = entry["content"]
            content.encode("utf-8")
            if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
                return f"New content for {relative} exceeds 1 MB. No project files were changed."
            if existed and b"\r\n" in before and "\r\n" not in content:
                content = content.replace("\r\n", "\n").replace("\n", "\r\n")
            after = content.encode("utf-8")
            if before == after:
                continue
            total += len(before) + len(after)
            if total > _MAX_PATCH_BYTES:
                return f"Patch exceeds the {_MAX_PATCH_BYTES // 1_000_000} MB total snapshot limit. No project files were changed."
            prepared.append({
                "path": relative,
                "existed": existed,
                "before_b64": base64.b64encode(before).decode("ascii"),
                "before_sha256": hashlib.sha256(before).hexdigest() if existed else None,
                "after_b64": base64.b64encode(after).decode("ascii"),
                "after_sha256": hashlib.sha256(after).hexdigest(),
            })
        except (OSError, UnicodeError, ValueError) as exc:
            return f"Could not stage patch: {exc}. No project files were changed."
    if not prepared:
        return "The proposed contents are identical to the current files; no patch was staged."
    diff = _render_patch(prepared)
    if len(diff) > _MAX_PATCH_PREVIEW:
        return f"Full diff is {len(diff)} characters, above the {_MAX_PATCH_PREVIEW}-character preview limit. Split this into smaller patches; nothing was staged or changed."
    patch_id = uuid.uuid4().hex[:10]
    record = {
        "id": patch_id,
        "state": "staged",
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "changes": prepared,
        "diff": diff,
    }
    try:
        _save_patch(root, record)
        saved = sorted(_patch_dir(root).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in saved[_MAX_SAVED_PATCHES:]:
            stale.unlink(missing_ok=True)
    except OSError as exc:
        return f"Could not save patch preview: {exc}. No project files were changed."
    return f"Staged patch {patch_id}; {len(prepared)} file(s). Nothing in the project has changed yet.\nFull diff:\n{diff}\n\nTo apply, call project_workspace action=apply_patch with patch_id={patch_id}; the HUD will show this diff for confirmation."


def _apply_staged_patch(root: Path, patch_id: str) -> str:
    record = _load_patch(root, patch_id)
    if record.get("state") != "staged":
        return f"Patch {patch_id} is {record.get('state')}; it was not applied."
    changes = record.get("changes")
    if not isinstance(changes, list) or not changes:
        return "Saved patch is empty or invalid; no files were changed."
    targets = []
    for item in changes:
        path, relative = _patch_relative_path(root, str(item.get("path") or ""))
        if relative != item.get("path"):
            return f"Patch path changed after staging; no files were changed: {relative}."
        existed = path.exists()
        if existed != bool(item.get("existed")):
            return f"{relative} changed since preview; no files were changed. Stage a new diff."
        current = path.read_bytes() if existed else b""
        current_hash = hashlib.sha256(current).hexdigest() if existed else None
        if current_hash != item.get("before_sha256"):
            return f"{relative} changed since preview; no files were changed. Stage a new diff."
        targets.append((path, current if existed else None, _decode_blob(item["after_b64"])))
    applied = []
    try:
        for path, before, after in targets:
            _write_bytes(path, after)
            applied.append((path, before))
        record["state"] = "applied"
        record["applied"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _save_patch(root, record)
    except Exception as exc:
        rollback_errors = []
        for path, before in reversed(applied):
            try:
                _write_bytes(path, before)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path.name}: {rollback_exc}")
        detail = f"Patch apply failed and attempted rollback: {exc}."
        if rollback_errors:
            detail += " Rollback problems: " + "; ".join(rollback_errors)
        return detail
    push_undo(f"project patch {patch_id} ({len(targets)} files)", lambda: _undo_patch(root, patch_id))
    return f"Applied patch {patch_id} to {len(targets)} file(s). Persistent backup is saved; use project_workspace action=undo_patch with patch_id={patch_id}, or the general undo action."


def _undo_patch(root: Path, patch_id: str) -> str:
    record = _load_patch(root, patch_id)
    if record.get("state") != "applied":
        return f"Patch {patch_id} is {record.get('state')}; nothing was reverted."
    targets = []
    for item in record.get("changes", []):
        path, relative = _patch_relative_path(root, str(item.get("path") or ""))
        current_exists = path.exists()
        current = path.read_bytes() if current_exists else b""
        current_hash = hashlib.sha256(current).hexdigest() if current_exists else None
        if current_hash != item.get("after_sha256"):
            return f"{relative} was edited after patch {patch_id}; refusing to overwrite that newer work. Nothing was reverted."
        before = _decode_blob(item["before_b64"]) if item["existed"] else None
        targets.append((path, current if current_exists else None, before))
    reverted = []
    try:
        for path, current, before in targets:
            _write_bytes(path, before)
            reverted.append((path, current))
        record["state"] = "undone"
        record["undone"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _save_patch(root, record)
    except Exception as exc:
        rollback_errors = []
        for path, current in reversed(reverted):
            try:
                _write_bytes(path, current)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path.name}: {rollback_exc}")
        detail = f"Patch rollback failed and attempted to restore the applied files: {exc}."
        if rollback_errors:
            detail += " Recovery problems: " + "; ".join(rollback_errors)
        return detail
    return f"Restored the pre-patch contents of {len(targets)} file(s)."


def _latest_patch_id(root: Path, state: str) -> str:
    try:
        records = []
        for path in _patch_dir(root).glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("state") == state:
                    records.append(record)
            except (OSError, ValueError, AttributeError):
                continue
        records.sort(key=lambda item: item.get("applied") or item.get("created") or "", reverse=True)
        return str(records[0].get("id") or "") if records else ""
    except OSError:
        return ""


def _list_patches(root: Path) -> str:
    try:
        records = []
        for path in _patch_dir(root).glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                records.append(record)
            except (OSError, ValueError, AttributeError):
                continue
        records.sort(key=lambda item: item.get("applied") or item.get("created") or "", reverse=True)
        if not records:
            return "No staged or applied project patches."
        return "Saved project patches:\n" + "\n".join(
            f"{item.get('id')} — {item.get('state')} — {len(item.get('changes', []))} file(s)"
            for item in records[:10]
        )
    except OSError:
        return "No saved project patches."


def _load_tasks(root: Path) -> dict:
    try:
        value = json.loads(_task_file(root).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_tasks(root: Path, tasks: dict) -> None:
    path = _task_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _process_running(record: dict) -> bool:
    try:
        import psutil
    except ImportError:
        return record.get("state", "running") == "running"
    try:
        process = psutil.Process(int(record["pid"]))
        expected = record.get("created")
        return process.is_running() and (expected is None or abs(process.create_time() - float(expected)) < 2)
    except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, ValueError, TypeError):
        return False


def _launch_saved_task(root: Path, task_id: str, record: dict, tasks: dict) -> str:
    argv = _split_command(str(record.get("command") or ""))
    if not argv:
        return "Saved task has no valid command."
    task_dir = _task_dir(root) / "logs"
    task_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(str(record.get("log") or task_dir / f"{task_id}.log")).resolve()
    if task_dir.resolve() not in log_path.parents:
        return "Saved task log path is invalid."
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            argv, cwd=str(root), stdin=subprocess.DEVNULL, stdout=log_file,
            stderr=subprocess.STDOUT, shell=False, close_fds=True,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            if os.name == "nt" else 0,
            start_new_session=(os.name != "nt"),
        )
    try:
        import psutil
        created = psutil.Process(process.pid).create_time()
    except Exception:
        created = None
    record.update({"pid": process.pid, "created": created, "state": "running",
                   "log": str(log_path), "started": datetime.now().astimezone().isoformat(timespec="seconds")})
    tasks[task_id] = record
    _save_tasks(root, tasks)
    return f"Started project task {task_id} (PID {process.pid}). Its process and log survive JARVIS restarts; use task_status or task_log to resume monitoring."


def _start_task(root: Path, command: str) -> str:
    argv = _split_command(command)
    if not argv:
        return "No command was provided."
    task_id = uuid.uuid4().hex[:10]
    tasks = _load_tasks(root)
    task_dir = _task_dir(root) / "logs"
    tasks[task_id] = {"state": "queued", "command": command[:1000], "log": str(task_dir / f"{task_id}.log"),
                      "queued_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    return _launch_saved_task(root, task_id, tasks[task_id], tasks)


def _enqueue_task(root: Path, command: str) -> str:
    if not _split_command(command):
        return "No valid command was provided."
    tasks = _load_tasks(root)
    queued = [item for item in tasks.values() if item.get("state") == "queued"]
    if len(queued) >= 50:
        return "The persistent project queue is full (50 commands). Start or cancel queued items first."
    task_id = uuid.uuid4().hex[:10]
    log_path = _task_dir(root) / "logs" / f"{task_id}.log"
    tasks[task_id] = {"state": "queued", "command": command[:1000], "log": str(log_path),
                      "queued_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    _save_tasks(root, tasks)
    return f"Queued project task {task_id}. It will not run until start_next_task is called and confirmed on the HUD."


def _queue_status(root: Path) -> str:
    tasks = _load_tasks(root)
    rows = []
    for task_id, record in tasks.items():
        state = record.get("state", "running")
        if state == "running" and not _process_running(record):
            state = "finished"
            record["state"] = state
        if state in {"queued", "running"}:
            rows.append((record.get("queued_at") or record.get("started") or "", task_id, record, state))
    if any(record.get("state") == "finished" for record in tasks.values()):
        _save_tasks(root, tasks)
    rows.sort(key=lambda item: item[0])
    if not rows:
        return "The persistent project queue is empty; no project task is running."
    return "Persistent project queue (FIFO):\n" + "\n".join(
        f"{task_id} [{state}] {record.get('command', '')}" for _, task_id, record, state in rows
    )


def _start_next_task(root: Path, task_id: str = "") -> str:
    tasks = _load_tasks(root)
    if any(record.get("state", "running") == "running" and _process_running(record) for record in tasks.values()):
        return "A queued project task is already running. Check task_status before starting another."
    candidates = [(record.get("queued_at") or "", ident, record)
                  for ident, record in tasks.items() if record.get("state") == "queued"]
    candidates.sort(key=lambda item: item[0])
    if task_id:
        selected = next((item for item in candidates if item[1] == task_id), None)
        if selected is None:
            return f"Task {task_id} is not queued. Use task_queue to inspect queued items."
    elif candidates:
        selected = candidates[0]
    else:
        return "There are no queued project tasks."
    _, ident, record = selected
    return _launch_saved_task(root, ident, record, tasks)


def _cancel_queued_task(root: Path, task_id: str) -> str:
    tasks = _load_tasks(root)
    record = tasks.get(task_id)
    if not record or record.get("state") != "queued":
        return f"Task {task_id} is not in the queue."
    record["state"] = "cancelled"
    record["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _save_tasks(root, tasks)
    return f"Cancelled queued project task {task_id}."


def _task_status(root: Path, task_id: str = "") -> str:
    tasks = _load_tasks(root)
    if task_id and task_id not in tasks:
        return f"No saved project task with ID {task_id}."
    selected = {task_id: tasks[task_id]} if task_id else tasks
    if not selected:
        return "No saved project tasks."
    try:
        import psutil  # noqa: F401
    except ImportError:
        return "Process status requires psutil, which is not installed."
    output = []
    for ident, record in selected.items():
        running = record.get("state", "running") == "running" and _process_running(record)
        state = record.get("state", "running")
        if state == "running" and not running:
            record["state"] = "finished"
            state = "finished"
        log_path = Path(str(record.get("log") or "")).resolve()
        if _task_dir(root).resolve() not in log_path.parents:
            tail = "(invalid saved log path)"
            output.append(f"Task {ident}: {state}\n{tail}")
            continue
        try:
            with log_path.open("rb") as stream:
                stream.seek(max(0, log_path.stat().st_size - 5000))
                tail = stream.read().decode("utf-8", errors="replace").strip()
        except OSError:
            tail = "(no output captured)"
        output.append(
            f"Task {ident}: {state}\n"
            f"Command: {record.get('command', '')}\nStarted: {record.get('started', 'unknown')}\n"
            f"Recent output:\n{tail or '(no output yet)'}"
        )
    _save_tasks(root, tasks)
    return "\n\n".join(output)[-_MAX_OUTPUT_CHARS:]


def _task_log(root: Path, task_id: str, offset: int = 0, max_chars: int = 12_000) -> str:
    record = _load_tasks(root).get(task_id)
    if not record:
        return f"No saved project task with ID {task_id}."
    try:
        offset = max(0, int(offset))
        limit = max(1000, min(int(max_chars), 16_000))
    except (TypeError, ValueError):
        return "offset and max_chars must be integers."
    log_path = Path(str(record.get("log") or "")).resolve()
    if _task_dir(root).resolve() not in log_path.parents:
        return "The saved task log path is invalid."
    try:
        size = log_path.stat().st_size
        if offset > size:
            return f"offset is beyond the end of the log ({size} bytes)."
        with log_path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(limit)
    except OSError as exc:
        return f"Could not read task log: {exc}"
    next_offset = offset + len(data)
    return (
        f"Task {task_id} log bytes {offset}–{next_offset} of {size}; "
        f"next offset={next_offset if next_offset < size else 'EOF'}\n"
        f"{data.decode('utf-8', errors='replace') or '(no output in this range)'}"
    )[:_MAX_OUTPUT_CHARS]


def _stop_task(root: Path, task_id: str) -> str:
    record = _load_tasks(root).get(task_id)
    if not record:
        return f"No saved project task with ID {task_id}."
    if record.get("state") == "queued":
        return f"Task {task_id} has not started. Use cancel_queued_task to remove it from the queue."
    if record.get("state") in {"finished", "stopped", "cancelled"}:
        return f"Task {task_id} is already {record['state']}."
    try:
        import psutil

        process = psutil.Process(int(record["pid"]))
        expected = record.get("created")
        if expected is None:
            return "Process identity was not recorded; refusing to stop a PID that could have been reused."
        if abs(process.create_time() - float(expected)) >= 2:
            return "The saved PID now belongs to a different process; it was not stopped."
        children = process.children(recursive=True)
        for child in reversed(children):
            child.terminate()
        process.terminate()
        _, alive = psutil.wait_procs(children + [process], timeout=4)
        for item in alive:
            item.kill()
        record["state"] = "stopped"
        record["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _save_tasks(root, _load_tasks(root) | {task_id: record})
        return f"Stopped project task {task_id}."
    except psutil.NoSuchProcess:
        return f"Project task {task_id} is already stopped."
    except (psutil.AccessDenied, OSError, ValueError, TypeError) as exc:
        return f"Could not stop project task {task_id}: {exc}"


def project_workspace(parameters: dict, **_context) -> str:
    p = parameters or {}
    action = str(p.get("action") or "current").strip().lower()
    workspace_arg = str(p.get("workspace") or "").strip()

    if action == "set_workspace":
        try:
            root = _resolve_workspace(workspace_arg)
            _WORKSPACE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _WORKSPACE_FILE.write_text(json.dumps({"path": str(root)}, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"Project workspace selected: {root}"
        except (OSError, ValueError) as exc:
            return str(exc)
    if action == "current":
        try:
            return f"Current project workspace: {_resolve_workspace(workspace_arg)}"
        except (OSError, ValueError) as exc:
            return str(exc)

    try:
        root = _resolve_workspace(workspace_arg)
        if action == "inspect":
            return _inspect(root)
        if action == "search":
            return _search(root, str(p.get("query") or ""))
        if action == "read":
            return _read(root, str(p.get("path") or ""))
        if action == "git_status":
            return _git(root, "status", "--short", "--branch")
        if action == "git_diff":
            relative = str(p.get("path") or "")
            args = ["diff", "HEAD", "--"]
            if relative:
                args.append(_target(root, relative).relative_to(root).as_posix())
            return _git(root, *args)[:_MAX_OUTPUT_CHARS]
        if action == "replace":
            return _replace(
                root, str(p.get("path") or ""), str(p.get("old_text") or ""),
                str(p.get("new_text") or ""),
            )
        if action == "stage_patch":
            return _stage_patch(root, p.get("changes"))
        if action == "apply_patch":
            patch_id = str(p.get("patch_id") or "").strip().lower()
            if not patch_id:
                return "A staged patch_id is required. Call patch_status to find saved patches."
            record = _load_patch(root, patch_id)
            if record.get("state") != "staged":
                return f"Patch {patch_id} is {record.get('state')}; nothing was applied."
            if pending_title():
                return "Another confirmation is already waiting on screen. Resolve it before applying this patch."
            detail = f"Workspace: {root}\nPatch ID: {patch_id}\n\n{record.get('diff', '')}"
            return request_confirmation(
                key=f"project-patch-{patch_id}",
                title=f"Apply project patch {patch_id}",
                detail=detail,
                run=lambda: _apply_staged_patch(root, patch_id),
            )
        if action == "undo_patch":
            patch_id = str(p.get("patch_id") or "").strip().lower()
            if not patch_id:
                patch_id = _latest_patch_id(root, "applied")
            if not patch_id:
                return "There is no applied saved project patch to undo."
            return _undo_patch(root, patch_id)
        if action == "patch_status":
            patch_id = str(p.get("patch_id") or "").strip().lower()
            if not patch_id:
                return _list_patches(root)
            record = _load_patch(root, patch_id)
            return f"Patch {patch_id}: {record.get('state')}\nFull diff:\n{record.get('diff', '')}"
        if action == "run":
            command = str(p.get("command") or "").strip()
            if not command:
                return "A command is required."
            if pending_title():
                return "Another confirmation is already waiting on screen. Resolve it before starting a command."
            return request_confirmation(
                key="project-workspace-command",
                title="Run a command in the selected project",
                detail=f"{command}\n\nWorking directory: {root}",
                run=lambda: _run_command(root, command),
            )
        if action == "start_task":
            command = str(p.get("command") or "").strip()
            if not command:
                return "A command is required."
            if pending_title():
                return "Another confirmation is already waiting on screen. Resolve it before starting a task."
            return request_confirmation(
                key="project-workspace-start-task",
                title="Start a background project task",
                detail=f"{command}\n\nWorking directory: {root}",
                run=lambda: _start_task(root, command),
            )
        if action == "queue_task":
            command = str(p.get("command") or "").strip()
            if not command or not _split_command(command):
                return "A valid command is required."
            if pending_title():
                return "Another confirmation is already waiting on screen. Resolve it before queueing a command."
            return request_confirmation(
                key="project-workspace-queue-task",
                title="Add command to the persistent project queue",
                detail=f"{command}\n\nWorking directory: {root}\nThis only queues the command; it will not run until separately confirmed.",
                run=lambda: _enqueue_task(root, command),
            )
        if action == "task_queue":
            return _queue_status(root)
        if action == "start_next_task":
            tasks = _load_tasks(root)
            if any(record.get("state", "running") == "running" and _process_running(record) for record in tasks.values()):
                return "A queued project task is already running. Check task_status before starting another."
            requested_id = str(p.get("task_id") or "").strip()
            candidates = sorted(
                ((record.get("queued_at") or "", ident, record)
                 for ident, record in tasks.items() if record.get("state") == "queued"),
                key=lambda item: item[0],
            )
            selected = next((item for item in candidates if item[1] == requested_id), None) if requested_id else (candidates[0] if candidates else None)
            if selected is None:
                return f"Task {requested_id} is not queued." if requested_id else "There are no queued project tasks."
            _, ident, record = selected
            if pending_title():
                return "Another confirmation is already waiting on screen. Resolve it before starting a queued task."
            return request_confirmation(
                key=f"project-workspace-start-queued-{ident}",
                title=f"Start queued project task {ident}",
                detail=f"{record.get('command', '')}\n\nWorking directory: {root}\nThe task runs without a shell, writes its output to a persistent log, and may continue after JARVIS restarts.",
                run=lambda: _start_next_task(root, ident),
            )
        if action == "cancel_queued_task":
            task_id = str(p.get("task_id") or "").strip()
            return _cancel_queued_task(root, task_id) if task_id else "A task ID is required."
        if action == "task_status":
            return _task_status(root, str(p.get("task_id") or "").strip())
        if action == "task_log":
            task_id = str(p.get("task_id") or "").strip()
            return _task_log(root, task_id, p.get("offset", 0), p.get("max_chars", 12_000)) if task_id else "A task ID is required."
        if action == "stop_task":
            task_id = str(p.get("task_id") or "").strip()
            return _stop_task(root, task_id) if task_id else "A task ID is required."
        return "Unknown action. Use current, set_workspace, inspect, search, read, git_status, git_diff, replace, stage_patch, apply_patch, undo_patch, patch_status, run, start_task, queue_task, task_queue, start_next_task, cancel_queued_task, task_status, task_log, or stop_task."
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return f"Project workspace operation failed: {exc}"


TOOL = {
    "name": "project_workspace",
    "description": (
        "Works in a user-selected project like a local coding agent: select and inspect a workspace, "
        "read/search files, inspect Git status/diff, make a unique reversible text replacement, "
        "or stage a multi-file patch using complete UTF-8 file contents, show its full diff, and apply only after explicit HUD confirmation. "
        "Applied patches keep a persistent local backup and can be rolled back with undo_patch, including after a JARVIS restart; rollback refuses to overwrite files changed since the patch. "
        "run a project command, start a background task after on-screen confirmation, save commands into a persistent FIFO queue, start the next queued command only after its own HUD confirmation, inspect or cancel queued tasks, check or stop saved tasks, and page through a task's full captured log by byte offset. Running subprocesses and queue records survive JARVIS restarts; a running command is monitored rather than silently restarted. Always inspect the project and its "
        "AGENTS.md before editing; verify changes with a diff. Paths are confined to the workspace."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "current | set_workspace | inspect | search | read | git_status | git_diff | replace | stage_patch | apply_patch | undo_patch | patch_status | run | start_task | queue_task | task_queue | start_next_task | cancel_queued_task | task_status | task_log | stop_task"},
            "workspace": {"type": "STRING", "description": "Project directory; omit to use the saved workspace"},
            "path": {"type": "STRING", "description": "Project-relative file path"},
            "query": {"type": "STRING", "description": "Literal text to search for"},
            "old_text": {"type": "STRING", "description": "Exact unique text fragment to replace"},
            "new_text": {"type": "STRING", "description": "Replacement text"},
            "changes": {"type": "ARRAY", "description": "For stage_patch: list of {path, content}, where content is the complete desired UTF-8 file text", "items": {"type": "OBJECT", "properties": {"path": {"type": "STRING"}, "content": {"type": "STRING"}}, "required": ["path", "content"]}},
            "patch_id": {"type": "STRING", "description": "ID returned by stage_patch; required for apply_patch and optional for patch_status/undo_patch"},
            "command": {"type": "STRING", "description": "One command line; runs without a shell and requires confirmation"},
            "task_id": {"type": "STRING", "description": "ID returned by start_task, for status or stopping"},
            "offset": {"type": "INTEGER", "description": "Byte offset for reading the next task_log page; start at 0"},
            "max_chars": {"type": "INTEGER", "description": "Maximum task_log page size (1000–16000 bytes)"},
        },
        "required": ["action"],
    },
    "handler": project_workspace,
}
