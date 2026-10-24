"""
MCP Server: OpenCode Integration for JARVIS

This server exposes OpenCode CLI capabilities as MCP tools,
allowing JARVIS to delegate coding, file operations, and
terminal tasks to OpenCode.
"""

import json
import subprocess
import sys
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

def _find_opencode() -> str:
    """Путь к opencode: сначала PATH, потом npm-глобал, потом исторический хардкод.

    Раньше здесь была строка с конкретным пользователем (C:\\Users\\RODION\\...),
    и на чужой машине (или после переименования профиля) сервер молча не находил
    CLI. Ищем по PATH, затем в типовых npm-каталогах."""
    import os
    import shutil as _shutil

    found = _shutil.which("opencode") or _shutil.which("opencode.cmd")
    if found:
        return found
    for base in (
        os.environ.get("APPDATA", ""),
        os.path.expanduser("~/AppData/Roaming"),
        os.path.expanduser("~/.npm-global/bin"),
    ):
        if not base:
            continue
        cand = Path(base) / "npm" / "opencode.cmd"
        if cand.exists():
            return str(cand)
    # Последний шанс: исторический путь этого экземпляра.
    return r"C:\Users\RODION\AppData\Roaming\npm\opencode.cmd"


OPENCODE_CMD = _find_opencode()
QUEUE_FILE = Path("C:/Temp/opencode/jarvis_queue.json")
JARVIS_RESPONSE_FILE = Path("C:/Temp/opencode/jarvis_response.json")

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "opencode_run",
        "description": (
            "Execute a command or task through OpenCode. "
            "Use this for coding tasks, file operations, terminal commands, "
            "web searches, browser automation, and complex multi-step operations. "
            "OpenCode has full access to the filesystem, terminal, and web."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task or command to execute (natural language or shell command)",
                },
                "mode": {
                    "type": "string",
                    "description": "Execution mode: 'command' for direct commands, 'agent' for autonomous task completion",
                    "enum": ["command", "agent"],
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 300)",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "opencode_exec",
        "description": (
            "Execute a shell command directly through OpenCode's terminal. "
            "Faster than opencode_run for simple commands."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory (optional)",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "opencode_status",
        "description": "Check if OpenCode CLI is available and responsive.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "opencode_help",
        "description": "Get information about OpenCode capabilities and available tools.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── Tool handlers ─────────────────────────────────────────────────────────────

def _opencode_run(task: str, mode: str = "agent", timeout: int = 300) -> str:
    """Run a task through OpenCode."""
    try:
        if mode == "command":
            cmd = [OPENCODE_CMD, "run", task]
        else:
            cmd = [OPENCODE_CMD, "run", "--autonomous", task]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        
        output = result.stdout.strip()
        if result.stderr.strip():
            output += f"\n[stderr]: {result.stderr.strip()}"
        
        if result.returncode != 0:
            return f"OpenCode exited with code {result.returncode}:\n{output}"
        
        return output or "OpenCode completed the task with no output."
    
    except subprocess.TimeoutExpired:
        return f"OpenCode task timed out after {timeout} seconds."
    except FileNotFoundError:
        return "OpenCode CLI not found. Make sure 'opencode' is in PATH."
    except Exception as e:
        return f"Error running OpenCode: {e}"


def _opencode_exec(command: str, cwd: str = None) -> str:
    """Execute a shell command directly."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
            encoding="utf-8",
            errors="replace",
        )
        
        output = result.stdout.strip()
        if result.stderr.strip():
            output += f"\n[stderr]: {result.stderr.strip()}"
        
        return output or "(no output)"
    
    except subprocess.TimeoutExpired:
        return "Command timed out after 60 seconds."
    except Exception as e:
        return f"Error executing command: {e}"


def _opencode_status() -> str:
    """Check OpenCode CLI status."""
    try:
        result = subprocess.run(
            [OPENCODE_CMD, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        version = result.stdout.strip()
        return f"OpenCode CLI is available. Version: {version}"
    except Exception as e:
        return f"OpenCode CLI not available: {e}"


def _opencode_help() -> str:
    """Get OpenCode capabilities info."""
    return """OpenCode Capabilities:

**File Operations:**
- Read, write, edit any file
- Search file contents and patterns
- Git operations (commit, push, pull, etc.)

**Terminal:**
- Execute any shell command
- Run background processes
- Install packages

**Code Intelligence:**
- Navigate and understand codebases
- Refactor code
- Debug issues
- Write tests

**Web & Research:**
- Search the web
- Fetch and analyze web pages
- Browser automation

**Multi-Agent:**
- Spawn sub-agents for complex tasks
- Parallel task execution
- Task delegation and coordination

**Integration:**
- Works with JARVIS via MCP
- Can be called from JARVIS voice commands
- Supports bidirectional task exchange

**Usage Examples:**
- "Refactor the authentication module"
- "Search for all TODO comments in src/"
- "Install pandas and create a data analysis script"
- "Open example.com and summarize the main content"
"""


# ── MCP Protocol Handler ─────────────────────────────────────────────────────

def handle_request(request: dict) -> dict:
    """Handle an MCP protocol request."""
    method = request.get("method", "")
    params = request.get("params", {})
    request_id = request.get("id")
    
    try:
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
        
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            
            if tool_name == "opencode_run":
                result = _opencode_run(
                    task=arguments.get("task", ""),
                    mode=arguments.get("mode", "agent"),
                    timeout=arguments.get("timeout", 300),
                )
            elif tool_name == "opencode_exec":
                result = _opencode_exec(
                    command=arguments.get("command", ""),
                    cwd=arguments.get("cwd"),
                )
            elif tool_name == "opencode_status":
                result = _opencode_status()
            elif tool_name == "opencode_help":
                result = _opencode_help()
            else:
                result = f"Unknown tool: {tool_name}"
            
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": result}],
                    "isError": False,
                },
            }
        
        elif method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "opencode-mcp", "version": "1.0.0"},
                },
            }
        
        elif method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}

        # Empty-but-valid answers so clients don't log "resources unavailable".
        elif method == "resources/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": []}}

        elif method == "resources/templates/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"resourceTemplates": []},
            }

        elif method == "prompts/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"prompts": []}}
        
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
    
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32603, "message": str(e)},
        }


# ── Main Entry Point ─────────────────────────────────────────────────────────

def main():
    """Run the MCP server in stdio mode."""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            
            request = json.loads(line)
            # Notifications (no "id") must not receive a response.
            if "id" not in request:
                continue
            response = handle_request(request)
            
            print(json.dumps(response), flush=True)
        
        except json.JSONDecodeError as e:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            print(json.dumps(error_response), flush=True)
        
        except Exception as e:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(e)},
            }
            print(json.dumps(error_response), flush=True)


if __name__ == "__main__":
    main()
