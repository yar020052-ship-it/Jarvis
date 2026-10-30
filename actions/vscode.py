import subprocess
import time
import shutil
from pathlib import Path


def _find_code() -> str | None:
    """Find the VSCode 'code' executable."""
    for cmd in ("code", "code.cmd"):
        if shutil.which(cmd):
            return cmd
    candidates = [
        r"C:\Users\{}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd".format(
            Path.home().name
        ),
        r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
        r"C:\Program Files (x86)\Microsoft VS Code\bin\code.cmd",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def open_in_vscode(parameters=None, response=None, player=None, session_memory=None) -> str:
    """Open a file or folder in Visual Studio Code."""
    params = parameters or {}
    path = str(params.get("path", "") or params.get("file_path", "")).strip()
    line = params.get("line")
    goto = bool(params.get("goto", False))

    code_exe = _find_code()
    if not code_exe:
        return "Visual Studio Code не найден. Установите VSCode и убедитесь, что 'code' доступен в PATH."

    args = [code_exe]
    if path:
        p = Path(path)
        if not p.exists():
            return f"Путь не существует: {path}"
        if goto and line and p.is_file():
            args.append(f"{path}:{line}")
        else:
            args.append(str(p))
    else:
        args.append(".")

    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if player:
            player.write_log(f"[VSCode] Opened: {path or '.'}")
        return f"Открыто в VSCode: {path or 'текущая папка'}."
    except Exception as e:
        return f"Не удалось открыть VSCode: {e}"


TOOL = {
    "name": "open_in_vscode",
    "description": "Opens a file or folder in Visual Studio Code. Can open a specific file at a given line number.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "path": {
                "type": "STRING",
                "description": "File or folder path to open in VSCode",
            },
            "line": {
                "type": "INTEGER",
                "description": "Line number to jump to (only for files)",
            },
            "goto": {
                "type": "BOOLEAN",
                "description": "Whether to jump to the specified line",
            },
        },
        "required": [],
    },
    "handler": open_in_vscode,
}