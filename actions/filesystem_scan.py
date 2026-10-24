"""Read-only, resumable index of file metadata on mounted local volumes."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import psutil


def _index_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "JARVIS" / "filesystem_index.sqlite3"


def _connect() -> sqlite3.Connection:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            extension TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            volume TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS files_name_idx ON files(name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS files_ext_idx ON files(extension COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS files_size_idx ON files(size DESC);
        CREATE TABLE IF NOT EXISTS directories (
            path TEXT PRIMARY KEY,
            volume TEXT NOT NULL,
            scanned INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS directories_pending_idx ON directories(scanned);
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    return db


def _get(db: sqlite3.Connection, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def _set(db: sqlite3.Connection, key: str, value: object) -> None:
    db.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def _volumes() -> list[tuple[str, str]]:
    roots: dict[str, str] = {}
    for part in psutil.disk_partitions(all=False):
        mount = os.path.abspath(part.mountpoint)
        if not mount or not os.path.isdir(mount):
            continue
        # Windows: include fixed and removable volumes; do not block a full
        # scan on a disconnected mapped network drive or optical drive.
        if os.name == "nt":
            try:
                import ctypes

                get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
                get_drive_type.argtypes = [ctypes.c_wchar_p]
                get_drive_type.restype = ctypes.c_uint
                drive_type = get_drive_type(mount)
                if drive_type not in (2, 3):  # removable or fixed
                    continue
            except Exception:
                pass
        roots[os.path.normcase(mount)] = mount
    return [(path, path) for path in sorted(roots.values(), key=str.casefold)]


def _drives() -> str:
    rows = []
    for root, _label in _volumes():
        try:
            usage = psutil.disk_usage(root)
            rows.append(
                f"{root} — свободно {usage.free / 1024**3:.1f} из "
                f"{usage.total / 1024**3:.1f} ГБ"
            )
        except (OSError, PermissionError):
            rows.append(f"{root} — сведения о свободном месте недоступны")
    return "Подключённые локальные тома:\n" + ("\n".join(rows) if rows else "тома не найдены")


def _start_scan(db: sqlite3.Connection) -> str:
    volumes = _volumes()
    if not volumes:
        return "Не найдено доступных локальных томов для сканирования."
    db.execute("DELETE FROM files")
    db.execute("DELETE FROM directories")
    db.executemany(
        "INSERT OR IGNORE INTO directories(path,volume,scanned) VALUES(?,?,0)",
        volumes,
    )
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    _set(db, "in_progress", "1")
    _set(db, "started", started)
    _set(db, "finished", "")
    _set(db, "errors", 0)
    _set(db, "skipped_reparse", 0)
    db.commit()
    return ""


def _scan_batch(db: sqlite3.Connection) -> str:
    if _get(db, "in_progress") != "1":
        message = _start_scan(db)
        if message:
            return message

    start = time.monotonic()
    processed = 0
    errors = int(_get(db, "errors", "0") or 0)
    skipped_reparse = int(_get(db, "skipped_reparse", "0") or 0)
    # Keep each tool call responsive; the next `scan` call resumes where this
    # batch stopped. The complete drive tree is eventually covered.
    while processed < 400 and time.monotonic() - start < 12:
        row = db.execute(
            "SELECT path,volume FROM directories WHERE scanned=0 ORDER BY path LIMIT 1"
        ).fetchone()
        if row is None:
            _set(db, "in_progress", "0")
            _set(db, "finished", time.strftime("%Y-%m-%d %H:%M:%S"))
            db.commit()
            break

        directory, volume = row
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        attrs = getattr(stat, "st_file_attributes", 0)
                        is_reparse = bool(attrs & 0x400)  # Windows junction/symlink
                        if entry.is_symlink() or is_reparse:
                            skipped_reparse += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            db.execute(
                                "INSERT OR IGNORE INTO directories(path,volume,scanned) VALUES(?,?,0)",
                                (entry.path, volume),
                            )
                        elif entry.is_file(follow_symlinks=False):
                            suffix = Path(entry.name).suffix.lower()
                            db.execute(
                                "INSERT INTO files(path,name,extension,size,mtime,volume) "
                                "VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                                "name=excluded.name,extension=excluded.extension,"
                                "size=excluded.size,mtime=excluded.mtime,volume=excluded.volume",
                                (entry.path, entry.name, suffix, stat.st_size, stat.st_mtime, volume),
                            )
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
        db.execute("UPDATE directories SET scanned=1 WHERE path=?", (directory,))
        processed += 1
        if processed % 20 == 0:
            _set(db, "errors", errors)
            _set(db, "skipped_reparse", skipped_reparse)
            db.commit()

    _set(db, "errors", errors)
    _set(db, "skipped_reparse", skipped_reparse)
    db.commit()
    pending = db.execute("SELECT COUNT(*) FROM directories WHERE scanned=0").fetchone()[0]
    done = db.execute("SELECT COUNT(*) FROM directories WHERE scanned=1").fetchone()[0]
    if pending == 0 and _get(db, "in_progress") == "1":
        _set(db, "in_progress", "0")
        _set(db, "finished", time.strftime("%Y-%m-%d %H:%M:%S"))
        db.commit()
    file_count, total_bytes = db.execute(
        "SELECT COUNT(*),COALESCE(SUM(size),0) FROM files"
    ).fetchone()
    state = "идёт; вызовите scan ещё раз для продолжения" if pending else "завершено"
    return (
        f"Индексирование {state}. Обработано каталогов: {done}; в очереди: {pending}; "
        f"файлов: {file_count}; суммарный размер: {total_bytes / 1024**3:.2f} ГБ; "
        f"ошибок доступа: {errors}; пропущено ссылок/точек подключения: {skipped_reparse}. "
        f"Индекс хранится локально: {_index_path()}"
    )


def _status(db: sqlite3.Connection) -> str:
    row = db.execute(
        "SELECT COUNT(*),COALESCE(SUM(size),0) FROM files"
    ).fetchone()
    scanned = db.execute(
        "SELECT COUNT(*) FROM directories WHERE scanned=1"
    ).fetchone()[0]
    pending = db.execute(
        "SELECT COUNT(*) FROM directories WHERE scanned=0"
    ).fetchone()[0]
    state = "продолжается" if _get(db, "in_progress") == "1" else (
        "завершено" if _get(db, "finished") else "ещё не запускалось"
    )
    return (
        f"Состояние индекса: {state}; файлов: {row[0]}; каталогов обработано: {scanned}; "
        f"ожидают: {pending}; объём по метаданным: {row[1] / 1024**3:.2f} ГБ; "
        f"ошибки доступа: {_get(db, 'errors', '0')}; запуск: {_get(db, 'started', '—')}; "
        f"завершение: {_get(db, 'finished', '—') or '—'}"
    )


def _search(db: sqlite3.Connection, query: str, extension: str, limit: int) -> str:
    if not query and not extension:
        return "Для поиска укажите query (часть имени файла) или extension."
    clauses, args = [], []
    if query:
        clauses.append("instr(lower(name),lower(?))>0")
        args.append(query[:200])
    if extension:
        ext = extension.lower().strip()
        if not ext.startswith("."):
            ext = "." + ext
        clauses.append("extension=?")
        args.append(ext[:30])
    where = " AND ".join(clauses)
    total = db.execute(f"SELECT COUNT(*) FROM files WHERE {where}", args).fetchone()[0]
    rows = db.execute(
        f"SELECT path,size,mtime FROM files WHERE {where} ORDER BY path LIMIT ?",
        (*args, limit),
    ).fetchall()
    if not rows:
        return "Совпадений в индексе нет. " + (
            "Сначала запустите filesystem_scan action=scan." if not _get(db, "started") else ""
        )
    lines = [f"Найдено в индексе: {total}; показаны первые {len(rows)}."]
    lines.extend(f"{path} ({size / 1024:.1f} КБ; {time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))})" for path, size, mtime in rows)
    if _get(db, "in_progress") == "1":
        lines.append("Индекс ещё строится; повторите поиск после завершения сканирования.")
    elif _get(db, "finished"):
        lines.append(f"Индекс обновлён: {_get(db, 'finished')}.")
    return "\n".join(lines)


def _largest(db: sqlite3.Connection, limit: int) -> str:
    rows = db.execute(
        "SELECT path,size FROM files ORDER BY size DESC LIMIT ?", (limit,)
    ).fetchall()
    if not rows:
        return "Индекс пуст. Сначала запустите filesystem_scan action=scan."
    return "Крупнейшие файлы в индексе:\n" + "\n".join(
        f"{size / 1024**3:.2f} ГБ — {path}" for path, size in rows
    )


def filesystem_scan(parameters: dict | None = None) -> str:
    p = parameters or {}
    action = str(p.get("action", "status")).strip().lower()
    try:
        if action == "drives":
            return _drives()
        with closing(_connect()) as db:
            if action == "scan":
                return _scan_batch(db)
            if action == "status":
                return _status(db)
            if action == "search":
                limit = max(1, min(int(p.get("limit", 30)), 100))
                return _search(
                    db,
                    str(p.get("query", "")).strip(),
                    str(p.get("extension", "")).strip(),
                    limit,
                )
            if action == "largest":
                limit = max(1, min(int(p.get("limit", 20)), 100))
                return _largest(db, limit)
        return "action должен быть drives, scan, status, search или largest."
    except Exception as exc:
        return f"Сканирование файловой системы завершилось ошибкой: {exc}"


TOOL = {
    "name": "filesystem_scan",
    "description": (
        "Read-only local filesystem inventory. Use only when asked to scan the whole filesystem, "
        "find a file across drives, or list large files. First action=drives lists mounted local "
        "volumes. action=scan starts or resumes a metadata-only scan in short batches; call it "
        "again until complete. action=status reports progress. action=search queries the local "
        "index by filename fragment and/or extension; action=largest reports largest indexed files. "
        "Never reads file contents, never changes source files, and never sends the index over the network."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "drives | scan (start/resume a complete scan in a short batch) | status | search | largest",
            },
            "query": {"type": "STRING", "description": "Filename substring for search"},
            "extension": {"type": "STRING", "description": "File extension, e.g. .pdf"},
            "limit": {"type": "INTEGER", "description": "Maximum search/largest results (1-100)"},
        },
        "required": ["action"],
    },
    "handler": filesystem_scan,
}
