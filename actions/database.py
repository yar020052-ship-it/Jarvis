"""Database management for JARVIS — SQLite query execution and table management.

Allows running SQL queries against local SQLite databases.
"""

import sqlite3
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def query_database(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Execute a SQL query against a SQLite database."""
    params = parameters or {}
    db_path = params.get("db_path", "").strip()
    query = params.get("query", "").strip()
    params_list = params.get("params", [])

    if not db_path:
        return "Укажите путь к базе данных (db_path)."
    if not query:
        return "Укажите SQL-запрос (query)."

    p = Path(db_path)
    if not p.exists():
        return f"База данных не найдена: {db_path}"

    try:
        conn = sqlite3.connect(str(p))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query_upper = query.strip().upper()
        if query_upper.startswith("SELECT") or query_upper.startswith("PRAGMA") or query_upper.startswith("EXPLAIN"):
            cursor.execute(query, params_list or [])
            rows = cursor.fetchall()
            if not rows:
                return "Запрос выполнен, результатов нет."
            # Format as table
            columns = [desc[0] for desc in cursor.description]
            header = " | ".join(columns)
            separator = "-" * len(header)
            lines = [header, separator]
            for row in rows[:50]:  # Limit to 50 rows
                lines.append(" | ".join(str(row[col]) for col in columns))
            if len(rows) > 50:
                lines.append(f"... и ещё {len(rows) - 50} строк")
            conn.close()
            return "\n".join(lines)
        else:
            cursor.execute(query, params_list or [])
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return f"Запрос выполнен. Затронуто строк: {affected}."
    except Exception as e:
        return f"Ошибка SQL: {e}"


def list_tables(parameters=None, response=None, player=None, session_memory=None) -> str:
    """List tables in a SQLite database."""
    params = parameters or {}
    db_path = params.get("db_path", "").strip()

    if not db_path:
        return "Укажите путь к базе данных (db_path)."

    p = Path(db_path)
    if not p.exists():
        return f"База данных не найдена: {db_path}"

    try:
        conn = sqlite3.connect(str(p))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        if not tables:
            return "Таблиц в базе данных нет."
        return "Таблицы:\n" + "\n".join(f"  • {t}" for t in tables)
    except Exception as e:
        return f"Ошибка: {e}"


def describe_table(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Describe a table structure."""
    params = parameters or {}
    db_path = params.get("db_path", "").strip()
    table = params.get("table", "").strip()

    if not db_path or not table:
        return "Укажите db_path и table."

    p = Path(db_path)
    if not p.exists():
        return f"База данных не найдена: {db_path}"

    try:
        conn = sqlite3.connect(str(p))
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        columns = cursor.fetchall()
        conn.close()
        if not columns:
            return f"Таблица '{table}' не найдена."
        lines = [f"Таблица: {table}", "Колонки:"]
        for col in columns:
            lines.append(f"  • {col[1]} ({col[2]}){' PK' if col[5] else ''}{' NOT NULL' if col[3] else ''}")
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка: {e}"


TOOL = {
    "name": "database",
    "description": "Execute SQL queries and manage SQLite databases.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "query (execute SQL), tables (list tables), or describe (describe table)",
            },
            "db_path": {
                "type": "STRING",
                "description": "Path to SQLite database file",
            },
            "query": {
                "type": "STRING",
                "description": "SQL query to execute",
            },
            "table": {
                "type": "STRING",
                "description": "Table name (for describe)",
            },
            "params": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Query parameters (for parameterized queries)",
            },
        },
        "required": ["action", "db_path"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        query_database(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "query").lower() == "query"
        else (
            list_tables(parameters, response, player, session_memory)
            if (parameters or {}).get("action", "").lower() == "tables"
            else describe_table(parameters, response, player, session_memory)
        )
    ),
}