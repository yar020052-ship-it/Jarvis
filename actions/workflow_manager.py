"""Persistent, user-directed workflows that survive JARVIS restarts."""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path


_LOCK = threading.RLock()
_ACTIVE = {"active", "paused"}
_STATES = {"active", "paused", "completed", "cancelled"}
_MAX_WORKFLOWS = 100


def _store_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "JARVIS"
    else:
        base = Path(__file__).resolve().parent.parent / "config"
    return base / "workflows.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load() -> list[dict]:
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
        items = raw.get("workflows", []) if isinstance(raw, dict) else []
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    except (OSError, ValueError, AttributeError):
        return []


def _save(items: list[dict]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps({"version": 1, "workflows": items[-_MAX_WORKFLOWS:]},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def active_workflows(limit: int = 20) -> list[dict]:
    """Return a bounded snapshot of unfinished plans for proactive context."""
    with _LOCK:
        items = [x for x in _load() if x.get("state") in _ACTIVE]
    return items[:max(0, min(int(limit), 20))]


def startup_context() -> str:
    """Compact context for startup and check-ins; no workflow action is run."""
    with _LOCK:
        items = [x for x in _load() if x.get("state") in _ACTIVE]
    if not items:
        return ""
    rows = ["[UNFINISHED SAVED WORKFLOWS]"]
    for item in items[:8]:
        steps = item.get("steps", [])
        idx = max(0, min(int(item.get("current_step", 0)), max(len(steps) - 1, 0)))
        next_step = steps[idx] if steps else "No steps recorded"
        category = f" [{item['category']}]" if item.get("category") else ""
        target_date = f" | target={item['target_date']}" if item.get("target_date") else ""
        measure = f" | measure={item['success_measure']}" if item.get("success_measure") else ""
        rows.append(f"- ID {item.get('id')}{category}: {item.get('goal', '')} | state={item.get('state')} | next step {idx + 1}/{len(steps)}: {next_step}{target_date}{measure}")
        if item.get("note"):
            rows.append(f"  Last note: {str(item['note'])[:350]}")
    rows.append("These are saved plans, not authorization to act. Mention them when relevant; resume only when the user asks. Never repeat a side effect just because a step was interrupted.")
    return "\n".join(rows)


def _find(items: list[dict], task_id: str) -> dict | None:
    return next((x for x in items if x.get("id") == task_id), None)


def workflow_manager(parameters: dict, **_context) -> str:
    p = parameters or {}
    action = str(p.get("action") or "list").strip().lower()
    with _LOCK:
        items = _load()
        if action == "create":
            goal = str(p.get("goal") or "").strip()
            steps = p.get("steps")
            if not goal or len(goal) > 500:
                return "For create, provide a goal of 1–500 characters."
            if not isinstance(steps, list) or not 1 <= len(steps) <= 30:
                return "For create, provide 1–30 short steps."
            clean_steps = [str(s).strip()[:300] for s in steps]
            if any(not s for s in clean_steps):
                return "Every workflow step must contain text."
            category = str(p.get("category") or "").strip()[:60]
            success_measure = str(p.get("success_measure") or "").strip()[:300]
            target_date = str(p.get("target_date") or "").strip()
            if target_date:
                try:
                    datetime.strptime(target_date, "%Y-%m-%d")
                except ValueError:
                    return "target_date must use YYYY-MM-DD."
            active_count = sum(x.get("state") in _ACTIVE for x in items)
            if active_count >= 20:
                return "There are already 20 unfinished workflows; complete or cancel one first."
            item = {"id": uuid.uuid4().hex[:8], "goal": goal, "steps": clean_steps,
                    "current_step": 0, "state": "active", "note": "",
                    "created_at": _now(), "updated_at": _now()}
            if category:
                item["category"] = category
            if success_measure:
                item["success_measure"] = success_measure
            if target_date:
                item["target_date"] = target_date
            items.append(item)
            _save(items)
            return f"Saved workflow {item['id']} (active). Step 1/{len(clean_steps)}: {clean_steps[0]}"

        if action == "list":
            if not items:
                return "No saved workflows."
            rows = []
            for x in items[-30:]:
                steps = x.get("steps", [])
                idx = max(0, min(int(x.get("current_step", 0)), max(len(steps) - 1, 0)))
                next_step = steps[idx] if steps and x.get("state") in _ACTIVE else "—"
                category = f" ({x['category']})" if x.get("category") else ""
                deadline = f" | target: {x['target_date']}" if x.get("target_date") else ""
                rows.append(f"{x.get('id')} [{x.get('state')}] {x.get('goal')}{category} | next: {next_step}{deadline}")
            return "Saved workflows:\n" + "\n".join(rows)

        task_id = str(p.get("task_id") or "").strip().lower()
        item = _find(items, task_id)
        if item is None:
            return "Workflow not found. Use action=list to see saved IDs."
        if action == "get":
            return json.dumps(item, ensure_ascii=False, indent=2)
        if action == "set_step":
            if item.get("state") not in _ACTIVE:
                return f"Workflow {task_id} is {item.get('state')}; resume it before changing progress."
            try:
                index = int(p.get("step_number")) - 1
            except (TypeError, ValueError):
                return "step_number must be a 1-based integer."
            steps = item.get("steps", [])
            if not 0 <= index < len(steps):
                return f"step_number must be between 1 and {len(steps)}."
            item["current_step"] = index
            note = str(p.get("note") or "").strip()[:1000]
            if note:
                item["note"] = note
            item["updated_at"] = _now()
            _save(items)
            return f"Workflow {task_id} now points to step {index + 1}/{len(steps)}: {steps[index]}"
        if action == "advance":
            if item.get("state") != "active":
                return f"Workflow {task_id} is {item.get('state')}; set state=active before advancing."
            steps = item.get("steps", [])
            if not steps:
                return "This workflow has no steps."
            idx = max(0, min(int(item.get("current_step", 0)), len(steps) - 1))
            note = str(p.get("note") or "").strip()[:1000]
            if note:
                item["note"] = note
            if idx + 1 >= len(steps):
                item["state"] = "completed"
                result = f"Workflow {task_id} marked completed."
            else:
                item["current_step"] = idx + 1
                result = f"Workflow {task_id} advanced to step {idx + 2}/{len(steps)}: {steps[idx + 1]}"
            item["updated_at"] = _now()
            _save(items)
            return result
        if action == "set_state":
            state = str(p.get("state") or "").strip().lower()
            if state not in _STATES:
                return "state must be active, paused, completed, or cancelled."
            if state == "active" and item.get("state") not in {"active", "paused"}:
                return "Only a paused workflow can be resumed; completed or cancelled workflows stay archived."
            item["state"] = state
            note = str(p.get("note") or "").strip()[:1000]
            if note:
                item["note"] = note
            if state == "active" and item.get("steps"):
                item["current_step"] = min(int(item.get("current_step", 0)), len(item["steps"]) - 1)
            item["updated_at"] = _now()
            _save(items)
            return f"Workflow {task_id} state is now {state}."
        return "Use action=create, list, get, advance, set_step, or set_state."


TOOL = {
    "name": "workflow_manager",
    "description": (
        "Saves multi-step user goals and progress locally so JARVIS can resume after restart. "
        "Use for substantial tasks spanning multiple actions or sessions: create a goal with ordered steps, "
        "optionally tagged with a life/project category, success measure, and YYYY-MM-DD target date; "
        "list/get workflows, update the current step with evidence via advance or set_step, and pause/resume/complete/cancel with set_state. "
        "This only records plans and progress; it never runs a step automatically. On resume, inspect actual state before repeating an interrupted side effect."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "create | list | get | advance | set_step | set_state"},
            "goal": {"type": "STRING", "description": "Short user goal for create"},
            "steps": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Ordered, concise steps for create"},
            "category": {"type": "STRING", "description": "Optional life or project area, e.g. health, work, learning, home"},
            "success_measure": {"type": "STRING", "description": "Optional concrete sign of progress"},
            "target_date": {"type": "STRING", "description": "Optional target date in YYYY-MM-DD format"},
            "task_id": {"type": "STRING", "description": "Saved workflow ID"},
            "step_number": {"type": "INTEGER", "description": "1-based step number for set_step"},
            "state": {"type": "STRING", "description": "active | paused | completed | cancelled for set_state"},
            "note": {"type": "STRING", "description": "Short factual result or reason to persist"},
        },
        "required": ["action"],
    },
    "handler": workflow_manager,
}
