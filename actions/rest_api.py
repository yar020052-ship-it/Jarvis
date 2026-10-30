"""REST API server for JARVIS — exposes endpoints for external integrations.

Runs a lightweight HTTP server with JSON endpoints for status, commands,
and memory access.
"""

import json
import threading
import http.server
import socketserver
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


_api_server = None
_api_thread = None
_api_port = 8090


class ApiHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/status":
            self._json_response({"status": "ok", "assistant": "JARVIS"})
        elif self.path == "/api/health":
            self._json_response({"healthy": True})
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8", errors="replace")
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {"raw": body}

        if self.path == "/api/command":
            # Forward to JARVIS command queue (simplified)
            self._json_response({"status": "received", "command": data.get("command", "")})
        else:
            self._json_response({"error": "Not found"}, 404)

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        pass


def start_api_server(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Start the REST API server."""
    global _api_server, _api_thread, _api_port

    params = parameters or {}
    port = int(params.get("port", 8090))
    _api_port = port

    if _api_server is not None:
        return f"REST API сервер уже запущен на порту {_api_port}."

    try:
        _api_server = socketserver.TCPServer(("", port), ApiHandler)
        _api_thread = threading.Thread(target=_api_server.serve_forever, daemon=True)
        _api_thread.start()
        return (
            f"REST API сервер запущен на порту {port}.\n"
            f"GET /api/status — статус\n"
            f"GET /api/health — здоровье\n"
            f"POST /api/command — отправить команду"
        )
    except Exception as e:
        _api_server = None
        return f"Ошибка запуска REST API: {e}"


def stop_api_server(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Stop the REST API server."""
    global _api_server, _api_thread

    if _api_server is None:
        return "REST API сервер не запущен."

    try:
        _api_server.shutdown()
        _api_server = None
        _api_thread = None
        return "REST API сервер остановлен."
    except Exception as e:
        return f"Ошибка остановки: {e}"


TOOL = {
    "name": "rest_api",
    "description": "Start/stop a REST API server for external integrations.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start or stop",
            },
            "port": {
                "type": "INTEGER",
                "description": "Port to listen on (default: 8090)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        start_api_server(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "start").lower() == "start"
        else stop_api_server(parameters, response, player, session_memory)
    ),
}