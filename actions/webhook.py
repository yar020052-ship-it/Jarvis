"""Webhook receiver for JARVIS — accepts incoming HTTP webhooks.

Runs a lightweight HTTP server that receives POST requests and forwards
them to JARVIS as commands or notifications.
"""

import json
import threading
import http.server
import socketserver
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


_webhook_server = None
_webhook_thread = None
_webhook_port = 8099
_received_webhooks = []


class WebhookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8", errors="replace")
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {"raw": body}

        _received_webhooks.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": data,
        })

        # Keep only last 50
        if len(_received_webhooks) > 50:
            del _received_webhooks[:len(_received_webhooks) - 50]

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "received"}).encode())

    def do_GET(self):
        if self.path == "/webhooks":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_received_webhooks[-20:]).encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"JARVIS Webhook Receiver")

    def log_message(self, format, *args):
        pass  # Suppress logs


def start_webhook_server(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Start the webhook receiver server."""
    global _webhook_server, _webhook_thread, _webhook_port

    params = parameters or {}
    port = int(params.get("port", 8099))
    _webhook_port = port

    if _webhook_server is not None:
        return f"Вебхук-сервер уже запущен на порту {_webhook_port}."

    try:
        _webhook_server = socketserver.TCPServer(("", port), WebhookHandler)
        _webhook_thread = threading.Thread(target=_webhook_server.serve_forever, daemon=True)
        _webhook_thread.start()
        return f"Вебхук-сервер запущен на порту {port}. Отправляйте POST на http://localhost:{port}/<path>"
    except Exception as e:
        _webhook_server = None
        return f"Ошибка запуска вебхук-сервера: {e}"


def stop_webhook_server(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Stop the webhook receiver server."""
    global _webhook_server, _webhook_thread

    if _webhook_server is None:
        return "Вебхук-сервер не запущен."

    try:
        _webhook_server.shutdown()
        _webhook_server = None
        _webhook_thread = None
        return "Вебхук-сервер остановлен."
    except Exception as e:
        return f"Ошибка остановки: {e}"


def get_webhooks(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Get received webhooks."""
    if not _received_webhooks:
        return "Вебхуков не получено."
    recent = _received_webhooks[-10:]
    results = []
    for w in recent:
        results.append(f"Путь: {w['path']}\nДанные: {json.dumps(w['body'], ensure_ascii=False)[:500]}")
    return f"Последние вебхуки ({len(recent)}):\n\n" + "\n\n---\n\n".join(results)


TOOL = {
    "name": "webhook",
    "description": "Start/stop a webhook receiver server and view received webhooks.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start (start server), stop (stop server), or list (view received webhooks)",
            },
            "port": {
                "type": "INTEGER",
                "description": "Port to listen on (default: 8099)",
            },
        },
        "required": ["action"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        start_webhook_server(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "start").lower() == "start"
        else (
            stop_webhook_server(parameters, response, player, session_memory)
            if (parameters or {}).get("action", "").lower() == "stop"
            else get_webhooks(parameters, response, player, session_memory)
        )
    ),
}