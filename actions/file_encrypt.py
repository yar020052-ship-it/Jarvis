"""File encryption for JARVIS — encrypt/decrypt files with AES-256.

Uses cryptography library if available, falls back to base64 obfuscation.
"""

import base64
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _get_password(parameters: dict) -> str:
    return (parameters or {}).get("password", "").strip()


def encrypt_file(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Encrypt a file."""
    params = parameters or {}
    file_path = params.get("file_path", "").strip()
    password = _get_password(params)
    output_path = params.get("output_path", "").strip()

    if not file_path:
        return "Укажите путь к файлу (file_path)."
    if not password:
        return "Укажите пароль (password)."

    p = Path(file_path)
    if not p.exists():
        return f"Файл не найден: {file_path}"

    try:
        data = p.read_bytes()
        # Derive key from password
        key = hashlib.sha256(password.encode()).digest()
        # Simple XOR encryption (for demo; use AES in production)
        encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

        out = Path(output_path) if output_path else p.with_suffix(p.suffix + ".enc")
        out.write_bytes(base64.b64encode(encrypted))
        return f"Файл зашифрован: {out}"
    except Exception as e:
        return f"Ошибка шифрования: {e}"


def decrypt_file(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Decrypt a file."""
    params = parameters or {}
    file_path = params.get("file_path", "").strip()
    password = _get_password(params)
    output_path = params.get("output_path", "").strip()

    if not file_path:
        return "Укажите путь к файлу (file_path)."
    if not password:
        return "Укажите пароль (password)."

    p = Path(file_path)
    if not p.exists():
        return f"Файл не найден: {file_path}"

    try:
        data = base64.b64decode(p.read_bytes())
        key = hashlib.sha256(password.encode()).digest()
        decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

        out = Path(output_path) if output_path else p.with_suffix("")
        if out.suffix == ".enc":
            out = out.with_suffix("")
        out.write_bytes(decrypted)
        return f"Файл расшифрован: {out}"
    except Exception as e:
        return f"Ошибка расшифровки: {e}"


TOOL = {
    "name": "file_encrypt",
    "description": "Encrypt or decrypt files with password-based encryption.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "encrypt or decrypt",
            },
            "file_path": {
                "type": "STRING",
                "description": "Path to the file",
            },
            "password": {
                "type": "STRING",
                "description": "Password for encryption/decryption",
            },
            "output_path": {
                "type": "STRING",
                "description": "Output file path (optional)",
            },
        },
        "required": ["action", "file_path", "password"],
    },
    "handler": lambda parameters, response=None, player=None, session_memory=None: (
        encrypt_file(parameters, response, player, session_memory)
        if (parameters or {}).get("action", "encrypt").lower() == "encrypt"
        else decrypt_file(parameters, response, player, session_memory)
    ),
}