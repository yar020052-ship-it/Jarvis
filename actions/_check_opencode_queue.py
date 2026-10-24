"""
JARVIS Action: Check OpenCode task queue

This action is called periodically by JARVIS to check if OpenCode
has sent any tasks through the shared queue file.

Add this to JARVIS's proactive checks or call it manually.
"""

import json
import sys
import time
from pathlib import Path

QUEUE_FILE = Path("C:/Temp/opencode/jarvis_queue.json")
RESPONSE_FILE = Path("C:/Temp/opencode/jarvis_response.json")


def check_queue() -> dict:
    """Check for pending tasks from OpenCode."""
    if not QUEUE_FILE.exists():
        return {"has_tasks": False, "tasks": []}
    
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        tasks = data.get("tasks", [])
        pending = [t for t in tasks if t.get("status") == "pending"]
        
        return {
            "has_tasks": len(pending) > 0,
            "tasks": pending,
            "count": len(pending),
        }
    except:
        return {"has_tasks": False, "tasks": []}


def mark_task_completed(task_id: str, response: str):
    """Mark a task as completed and write response."""
    if not QUEUE_FILE.exists():
        return
    
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        for task in data.get("tasks", []):
            if task.get("id") == task_id:
                task["status"] = "completed"
                task["completed_at"] = time.time()
                task["response"] = response
                break
        
        QUEUE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        
        # Also write response file for OpenCode to read
        RESPONSE_FILE.write_text(json.dumps({
            "task_id": task_id,
            "response": response,
            "timestamp": time.time(),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    except:
        pass


def run(parameters: dict = None, ctx: dict = None) -> str:
    """Run the queue check action."""
    args = parameters or {}
    action = args.get("action", "check")
    
    if action == "check":
        result = check_queue()
        if result["has_tasks"]:
            tasks_text = "\n".join(
                f"- [{t.get('priority', 'normal')}] {t.get('task', 'No task description')}"
                for t in result["tasks"]
            )
            return f"OpenCode has sent {result['count']} task(s):\n{tasks_text}\n\nYou should process these tasks and respond to OpenCode."
        else:
            return "No pending tasks from OpenCode."
    
    elif action == "respond":
        task_id = args.get("task_id", "")
        response = args.get("response", "")
        if task_id and response:
            mark_task_completed(task_id, response)
            return f"Response sent to OpenCode for task {task_id}."
        return "Missing task_id or response."
    
    return "Unknown action. Use 'check' or 'respond'."


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "opencode_queue",
    "description": (
        "Check for tasks sent by OpenCode and respond to them. "
        "OpenCode can send commands to JARVIS through a shared queue. "
        "Use this to see what OpenCode needs and send back responses."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action to perform: 'check' for pending tasks, 'respond' to send a response",
                "enum": ["check", "respond"],
            },
            "task_id": {
                "type": "STRING",
                "description": "Task ID (required for 'respond' action)",
            },
            "response": {
                "type": "STRING",
                "description": "Response text (required for 'respond' action)",
            },
        },
        "required": ["action"],
    },
    "handler": run,
}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        result = check_queue()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Run with 'test' argument to check queue")
