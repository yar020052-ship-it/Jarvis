"""voice_command.py — run the owner's own saved phrases, without the model.

WHY THIS EXISTS
    The owner keeps a command library (jarvis_commands.py — the Luxify-style
    command centre built in the companion project): ~100 phrases, each one a
    small script of exact steps, in their own wording — "открой ютуб", "режим
    кино", "сделай тише". The engine modules are copied verbatim into this
    project's root; this file is the seam that lets THIS JARVIS use them.

TWO DOORS IN, ONE ENGINE
    1. The model. `voice_command` is a discovered action, so when the user
       speaks something that is one of the saved phrases, Gemini calls this
       tool and then says the report back. Nothing local decides that — the
       model does, exactly like every other tool here.
    2. Typed text. main.py asks fast_plan() first: a typed line that IS a saved
       phrase (score >= FAST_SCORE) runs locally in one hop, with no model
       turn. Anything below that score falls through to Gemini, so ordinary
       conversation is never captured by an approximate match. Set
       JARVIS_VOICE_COMMANDS_FAST=0 to switch this door off.

DANGEROUS STEPS KEEP THE GATE
    A command that shuts down, sleeps, locks or hibernates — or any command the
    owner marked confirm=true — still asks through core/confirm.py: the banner
    on the HUD, pressed by a hand. The model cannot forge it, and the typed
    fast path goes through the same gate — an irreversible step is never one
    keystroke away.

WHAT IS DELIBERATELY NOT PORTED
    The "ask"/"agent" step types expect the companion project's own agent
    layer; in this project they answer honestly that the agent is unavailable
    instead of guessing. "telegram" steps want the companion project's own
    bot; this project already has its own Telegram bridge (plugins/
    telegram_remote.py) and the two are not wired together on purpose.

FILES
    jarvis_commands.py, jarvis_packs.py, jarvis_voice.py, jarvis_tts.py and
    jarvis_windows.py in this project are copies from the companion project;
    the library itself lives in this project's .jarvis/commands/library.json.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

import jarvis_commands as jc  # noqa: E402

# A typed line runs locally only at this confidence: the fast path exists to
# spare a model round trip for the owner's exact phrases, never to guess at a
# sentence the model should have read.
FAST_SCORE = 0.90
# Below this the library has nothing to offer and the model answers normally.
SUGGEST_SCORE = 0.55
FAST_ENV = "JARVIS_VOICE_COMMANDS_FAST"

_lib_lock = threading.Lock()
_lib_state: dict = {"lib": None, "stamp": None}

# One echo, never two runs: after the typed door executes a phrase, the spoken
# confirmation sent to the model can make it call voice_command back with the
# same words ("громче" would then arrive twice). For a few seconds the model
# door answers "already done" instead of running it again. Repeats through the
# SAME door are always allowed — saying "громче" twice means two steps up.
_run_lock = threading.Lock()
_last_run: dict = {"norm": "", "at": 0.0, "door": ""}
_DEDUPE_WINDOW = 8.0


# ── library access ────────────────────────────────────────────────────────────


def _library() -> "jc.Library":
    """The command library, reloaded whenever library.json changes on disk."""
    path = jc.LIB_PATH
    try:
        stamp = (os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        stamp = None
    with _lib_lock:
        if _lib_state["lib"] is None or _lib_state["stamp"] != stamp:
            _lib_state["lib"] = jc.Library(path)
            _lib_state["stamp"] = stamp
        return _lib_state["lib"]


def _invalidate() -> None:
    """Forget the cache after a write (a pack install, say)."""
    with _lib_lock:
        _lib_state["lib"] = None
        _lib_state["stamp"] = None


# ── execution ────────────────────────────────────────────────────────────────


def _confirm(question: str) -> bool:
    """Ask through the HUD's confirmation gate — never on the Qt thread.

    The gate hands back whatever the approved operation returned, so the
    sentinel below is how a plain yes/no is read out of it. On any failure the
    answer is no: an irreversible step must not run because a question could
    not be asked.
    """
    try:
        from core import confirm as gate
    except Exception:
        return False
    try:
        result = gate.request_and_wait(
            "voice_command",
            "Голосовая команда",
            question,
            run=lambda: "__voice_command_approved__",
        )
    except Exception:
        return False
    return str(result) == "__voice_command_approved__"


def _context(player=None) -> "jc.ExecContext":
    """One ExecContext for this run: no local TTS (the model does the voice),
    the HUD gate for confirmations, step events to the console."""

    def on_event(kind: str, text: str) -> None:
        print(f"[VoiceCommands] {kind}: {text}")

    return jc.ExecContext(speak=False, confirm_fn=_confirm, on_event=on_event)


def _gate_matches(matches: list, ctx: "jc.ExecContext") -> Optional[str]:
    """Pre-approve commands whose danger is not declared with confirm=true.

    execute_command() asks by itself when the command carries confirm=true, so
    those are left alone here. Anything else whose steps are risky (shutdown,
    sleep, lock…) gets the same on-screen question before the engine starts —
    the owner's own commands must not be the quiet way around the gate.
    """
    for m in matches:
        cmd = m.command
        if getattr(cmd, "confirm", False):
            continue
        if any(jc._step_is_risky(s) for s in (cmd.steps or [])):
            if not ctx.confirm(f"Команда «{cmd.name}» содержит опасный шаг. Выполнить?"):
                return f"Отменено: «{cmd.name}»."
    return None


def _suggestions(phrase: str, lib: "jc.Library") -> str:
    cands = jc.match_phrase(phrase, lib, threshold=SUGGEST_SCORE)[:5]
    if not cands:
        return "В библиотеке команд нет ничего похожего."
    lines = [
        f"«{m.command.name}» — {', '.join(m.command.phrases[:3])}"
        for m in cands
    ]
    return "Похожие сохранённые команды: " + "; ".join(lines) + "."


def _recent_duplicate(phrase: str, door: str) -> bool:
    """True when the other door ran this same phrase moments ago."""
    with _run_lock:
        last = dict(_last_run)
    return (
        bool(last["door"])
        and last["door"] != door
        and last["norm"] == jc.normalize(phrase)
        and time.monotonic() - last["at"] < _DEDUPE_WINDOW
    )


def _note_run(phrase: str, door: str) -> None:
    with _run_lock:
        _last_run.update(norm=jc.normalize(phrase), at=time.monotonic(), door=door)


def _execute(phrase: str, player=None, threshold: float = jc.ACCEPT_SCORE,
             door: str = ""):
    """Match and run one phrase. Returns (RunResult|None, extra_text)."""
    lib = _library()
    matches = jc.match_phrase(phrase, lib, threshold=threshold)
    if not matches:
        return None, _suggestions(phrase, lib)
    ctx = _context(player)
    refusal = _gate_matches(matches, ctx)
    if refusal:
        return None, refusal
    res = jc.run_phrase(phrase, ctx, lib, threshold=threshold)
    if door:
        _note_run(phrase, door)
    return res, ""


# ── the model-facing tool ────────────────────────────────────────────────────


def voice_command(parameters, player=None):
    """Run a saved phrase and report back in plain words."""
    phrase = str((parameters or {}).get("phrase") or "").strip()
    if not phrase:
        return "Не передана фраза (phrase)."
    if _recent_duplicate(phrase, "voice"):
        return "Эта команда только что выполнена локально — повторно не запускаю."
    try:
        res, extra = _execute(phrase, player=player, door="voice")
    except Exception as e:  # noqa: BLE001 — a tool must never raise at the model
        return f"Сбой командного центра: {e}"
    if res is None:
        return (
            "Сохранённая команда не найдена. " + extra
            + " Отвечай на запрос пользователя своими обычными инструментами."
        )
    if not res.matched:
        return f"Не выполнено: {res.error or 'нет совпадения'}"
    names = ", ".join(f"«{m['name']}»" for m in res.matched)
    if not res.ok:
        return f"{names}: не получилось — {res.error}"
    parts = [f"Выполнено: {names}."]
    if res.log:
        parts.append("Шаги: " + "; ".join(res.log[-6:]) + ".")
    if res.reply:
        parts.append(res.reply)
    return " ".join(parts)[:900]


TOOL = {
    "name": "voice_command",
    "description": (
        "Run one of the owner's OWN saved commands — the personal command "
        "library (Command Centre). Prefer this over improvising whenever the "
        "request looks like one of the owner's routine phrases or scenes, "
        "for example «открой ютуб», «режим кино», «доброе утро», «сделай "
        "тише», «протокол гости», «диктовка». The library holds the owner's "
        "exact wording and their exact step sequence, so a saved command must "
        "always beat a hand-built equivalent. Pass the request as a phrase, "
        "as close to the owner's words as you can. If nothing matches, the "
        "result lists the closest saved commands and you answer normally. "
        "Dangerous saved commands still raise the on-screen confirmation "
        "themselves — never claim one is done before the banner is confirmed."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "phrase": {
                "type": "STRING",
                "description": "The owner's request as a phrase, e.g. 'режим кино'.",
            },
        },
        "required": ["phrase"],
    },
    "handler": voice_command,
}


# ── the typed-text door (called from main.py, never by the model) ────────────


def _fast_enabled() -> bool:
    value = os.environ.get(FAST_ENV, "1").strip().lower()
    return value not in ("0", "false", "no", "off")


def fast_plan(text: str) -> Optional[list]:
    """Names of the saved commands a typed line IS — or None to let it pass on.

    Strict on purpose (FAST_SCORE). A near-miss must go to the model the way
    it always has; only the owner's own phrases skip the round trip.
    """
    if not _fast_enabled():
        return None
    phrase = str(text or "").strip()
    if not phrase or len(phrase) > 200 or "\n" in phrase or phrase.startswith("/"):
        return None
    try:
        lib = _library()
        matches = jc.match_phrase(phrase, lib, threshold=FAST_SCORE)
    except Exception as e:  # noqa: BLE001
        print(f"[VoiceCommands] fast_plan: {e}")
        return None
    return [m.command.name for m in matches] or None


def fast_run(text: str, player=None) -> str:
    """Execute a line fast_plan() has claimed; returns the sentence to show."""
    phrase = str(text or "").strip()
    try:
        res, extra = _execute(phrase, player=player, threshold=FAST_SCORE,
                              door="fast")
    except Exception as e:  # noqa: BLE001
        return f"Сбой командного центра: {e}"
    if res is None:
        return extra or ""
    if not res.matched:
        return f"Не выполнено: {res.error or 'нет совпадения'}"
    names = ", ".join(f"«{m['name']}»" for m in res.matched)
    if not res.ok:
        return f"{names}: не получилось — {res.error}"
    out = f"{names} — готово."
    if res.reply:
        out += f" {res.reply}"
    return out
