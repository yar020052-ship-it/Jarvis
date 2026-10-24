"""
Send a task/command from OpenCode to JARVIS.

Usage:
    python send_to_jarvis.py "Turn off the lights"
    python send_to_jarvis.py "Play some music" --priority high
    python send_to_jarvis.py "What's the weather?" --wait-response

This writes a task to the shared queue file that JARVIS monitors.
"""

import json
import time
import argparse
from pathlib import Path

QUEUE_FILE = Path("C:/Temp/opencode/jarvis_queue.json")
RESPONSE_FILE = Path("C:/Temp/opencode/jarvis_response.json")


def send_task(task: str, priority: str = "normal", wait_response: bool = False, timeout: int = 60) -> dict:
    """Send a task to JARVIS via the shared queue."""
    
    # Ensure queue file exists
    if not QUEUE_FILE.exists():
        QUEUE_FILE.write_text(json.dumps({"tasks": []}))
    
    # Read current queue
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except:
        data = {"tasks": []}
    
    # Create task entry
    task_entry = {
        "id": f"task_{int(time.time() * 1000)}",
        "task": task,
        "priority": priority,
        "source": "opencode",
        "timestamp": time.time(),
        "status": "pending",
    }
    
    # Add to queue
    data["tasks"].append(task_entry)
    QUEUE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    
    print(f"[OpenCode → JARVIS] Task sent: {task}")
    print(f"  Task ID: {task_entry['id']}")
    print(f"  Priority: {priority}")
    
    # Wait for response if requested
    if wait_response:
        print(f"  Waiting for JARVIS response (timeout: {timeout}s)...")
        start = time.time()
        while time.time() - start < timeout:
            try:
                if RESPONSE_FILE.exists():
                    response_data = json.loads(RESPONSE_FILE.read_text(encoding="utf-8"))
                    if response_data.get("task_id") == task_entry["id"]:
                        print("  Response received:")
                        print(f"  {response_data.get('response', 'No response text')}")
                        return response_data
            except:
                pass
            time.sleep(0.5)
        
        print("  Timeout waiting for response")
        return {"task_id": task_entry["id"], "response": None, "timeout": True}
    
    return task_entry


def check_queue() -> list:
    """Check pending tasks in the queue."""
    if not QUEUE_FILE.exists():
        return []
    
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        pending = [t for t in data.get("tasks", []) if t.get("status") == "pending"]
        return pending
    except:
        return []


def main():
    parser = argparse.ArgumentParser(description="Send tasks from OpenCode to JARVIS")
    parser.add_argument("task", nargs="?", help="Task to send to JARVIS")
    parser.add_argument("--priority", choices=["low", "normal", "high", "urgent"], default="normal")
    parser.add_argument("--wait", action="store_true", help="Wait for JARVIS response")
    parser.add_argument("--timeout", type=int, default=60, help="Response timeout in seconds")
    parser.add_argument("--check", action="store_true", help="Check pending tasks")
    
    args = parser.parse_args()
    
    if args.check:
        pending = check_queue()
        if pending:
            print(f"Pending tasks ({len(pending)}):")
            for t in pending:
                print(f"  [{t['priority']}] {t['task']}")
        else:
            print("No pending tasks")
        return
    
    if not args.task:
        parser.print_help()
        return
    
    send_task(args.task, args.priority, args.wait, args.timeout)


if __name__ == "__main__":
    main()
