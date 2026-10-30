"""Calendar integration for JARVIS — Google Calendar and local ICS events.

Supports listing upcoming events and creating new events.
Credentials stored in config/api_keys.json.
"""

import json
from pathlib import Path
import sys
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _config() -> dict:
    try:
        p = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def list_events(parameters=None, response=None, player=None, session_memory=None) -> str:
    """List upcoming calendar events."""
    params = parameters or {}
    days = int(params.get("days", 7))
    max_results = int(params.get("max_results", 10))

    cfg = _config()
    api_key = cfg.get("google_calendar_api_key", "")
    calendar_id = cfg.get("google_calendar_id", "primary")

    if not api_key:
        return (
            "Google Calendar не настроен. Укажите google_calendar_api_key и "
            "google_calendar_id в config/api_keys.json. "
            "Получить ключ: https://console.cloud.google.com/apis/credentials"
        )

    try:
        import urllib.request
        import urllib.parse

        now = datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(days=days)).isoformat() + "Z"

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/"
            f"{urllib.parse.quote(calendar_id, safe='')}/events?"
            f"key={api_key}&timeMin={urllib.parse.quote(time_min)}&"
            f"timeMax={urllib.parse.quote(time_max)}&maxResults={max_results}&"
            f"singleEvents=true&orderBy=startTime"
        )

        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        events = data.get("items", [])
        if not events:
            return f"Событий на ближайшие {days} дней нет."

        results = []
        for ev in events:
            summary = ev.get("summary", "(без названия)")
            start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
            end = ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date", "")
            location = ev.get("location", "")
            desc = ev.get("description", "")
            line = f"• {summary}\n  Начало: {start}"
            if end and end != start:
                line += f"\n  Конец: {end}"
            if location:
                line += f"\n  Место: {location}"
            if desc:
                line += f"\n  Описание: {desc[:200]}"
            results.append(line)

        return f"События на ближайшие {days} дней ({len(results)}):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Ошибка получения календаря: {e}"


def create_event(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Create a new calendar event."""
    params = parameters or {}
    title = params.get("title", "").strip()
    start = params.get("start", "").strip()
    end = params.get("end", "").strip()
    description = params.get("description", "").strip()
    location = params.get("location", "").strip()

    if not title:
        return "Укажите название события (title)."
    if not start:
        return "Укажите время начала (start) в формате ISO 8601, например 2026-10-29T15:00:00."

    cfg = _config()
    access_token = cfg.get("google_calendar_access_token", "")
    calendar_id = cfg.get("google_calendar_id", "primary")

    if not access_token:
        return (
            "Google Calendar не настроен для записи. Укажите google_calendar_access_token "
            "в config/api_keys.json. Получить токен: https://developers.google.com/oauthplayground"
        )

    try:
        import urllib.request
        import urllib.parse

        event = {
            "summary": title,
            "start": {"dateTime": start, "timeZone": "Europe/Moscow"},
            "end": {"dateTime": end or start, "timeZone": "Europe/Moscow"},
        }
        if description:
            event["description"] = description
        if location:
            event["location"] = location

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/"
            f"{urllib.parse.quote(calendar_id, safe='')}/events"
        )
        data = json.dumps(event).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return f"Событие '{title}' создано. ID: {result.get('id', 'N/A')}"
    except Exception as e:
        return f"Ошибка создания события: {e}"


TOOL = {
    "name": "calendar",
    "description": "List upcoming calendar events and create new events via Google Calendar.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list (list events) or create (create event)",
            },
            "days": {
                "type": "INTEGER",
                "description": "Number of days ahead to list (default: 7)",
            },
            "max_results": {
                "type": "INTEGER",
                "description": "Max events to return (default: 10)",
            },
            "title": {
                "type": "STRING",
                "description": "Event title (for create)",
            },
            "start": {
                "type": "STRING",
                "description": "Start time ISO 8601 (for create), e.g. 2026-10-29T15:00:00",
            },
            "end": {
                "type": "STRING",
                "description": "End time ISO 8601 (for create)",
            },
            "description": {
                "type": "STRING",
                "description": "Event description (for create)",
            },
            "location": {
                "type": "STRING",
                "description": "Event location (for create)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        list_events(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "list").lower() == "list"
        else create_event(parameters, response, player, session_memory)
    ),
}