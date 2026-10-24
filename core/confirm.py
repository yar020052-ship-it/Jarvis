"""
core/confirm.py — a confirmation the model cannot forge.

THE PROBLEM WITH THE OLD GATE
    computer_settings guarded shutdown and restart like this:

        confirmed = str(params.get("confirmed", "")).lower()
        if confirmed not in ("yes", "true", "1", "confirm"):
            return "Please confirm by calling again with confirmed=yes."

    `confirmed` is a tool parameter, which means the *model* writes it. Nothing
    stops it from sending confirmed=yes on the first call, and nothing checks
    that a human was ever involved. It is a convention, not a gate — and its
    coverage was two actions, so deleting files and switching off the WiFi the
    assistant is talking over went through with no gate at all.

THE DESIGN HERE
    The confirmation token is issued by the *interface*, never by the model:

      1. An action calls `request(...)` with a callable that does the real work.
      2. This module hands the UI a banner with CONFIRM / CANCEL and returns
         IMMEDIATELY with a sentence for the model to say out loud.
      3. If — and only if — the user presses CONFIRM, the UI calls `resolve()`,
         which runs the stored callable off the Qt thread.

    Nothing blocks. The model keeps talking while the banner is up, so this
    costs no latency at all; in fact it is cheaper than the old gate, which
    burned two tool round trips (reject, then re-call) on every shutdown.

WHAT BELONGS HERE AND WHAT DOES NOT
    Only genuinely irreversible things. Anything that can be reversed should be
    done at once and pushed onto core/undo.py instead — undo is faster than a
    question, and an assistant that asks before every action is one nobody uses.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

# A pending confirmation is abandoned after this long. Chosen to outlast a
# normal "hang on, let me look at the screen" pause without leaving a live
# shutdown button sitting on the HUD for the rest of the day.
TIMEOUT_SECONDS = 90.0


@dataclass
class _Pending:
    key: str
    title: str
    detail: str
    run: Callable[[], object]
    at: float
    on_resolve: Callable[[bool, object], None] | None = None


_pending: Optional[_Pending] = None
_lock = threading.Lock()

# Set once at startup by main.py. Signature: (title, detail) -> None for show,
# and () -> None for hide. Both are marshalled onto the Qt thread by the UI.
_show_cb: Optional[Callable[[str, str], None]] = None
_hide_cb: Optional[Callable[[], None]] = None
_log_cb: Optional[Callable[[str], None]] = None


def bind(show, hide, log=None) -> None:
    """Wire this module to the HUD. Called once from main.py at startup."""
    global _show_cb, _hide_cb, _log_cb
    _show_cb, _hide_cb, _log_cb = show, hide, log


def _log(msg: str) -> None:
    if _log_cb:
        try:
            _log_cb(msg)
        except Exception:
            pass


def request(
    key: str,
    title: str,
    detail: str,
    run: Callable[[], object],
    on_resolve: Callable[[bool, object], None] | None = None,
) -> object:
    """Park an irreversible action behind the on-screen gate.

    Returns the sentence the tool should hand back to the model — phrased as an
    instruction so the assistant asks the user out loud in their own language,
    rather than reading an English string verbatim."""
    global _pending

    if _show_cb is None:
        # No interface bound (headless, or a very early call). Refuse rather
        # than silently performing something irreversible.
        return (
            f"I cannot confirm '{title}' right now because the interface is "
            f"not available, so I have not done it."
        )

    with _lock:
        if _pending is not None and time.monotonic() - _pending.at <= TIMEOUT_SECONDS:
            return (
                "There is already a confirmation waiting on screen. "
                "Resolve it before requesting another operation."
            )
        _pending = _Pending(
            key=key, title=title, detail=detail, run=run,
            at=time.monotonic(), on_resolve=on_resolve,
        )

    try:
        _show_cb(title, detail)
    except Exception as e:
        with _lock:
            _pending = None
        return f"Could not ask for confirmation: {e}. Nothing was done."

    _log(f"SYS: Awaiting confirmation — {title}")
    return (
        f"[CONFIRMATION_PENDING] I have put a confirmation on screen for: {title}. "
        f"Say ONE short sentence in the user's own language telling them you need "
        f"them to confirm it on the HUD before you do it. Do not claim it is done."
    )


def request_and_wait(
    key: str,
    title: str,
    detail: str,
    run: Callable[[], str],
    wait_timeout: float = TIMEOUT_SECONDS + 120.0,
) -> str:
    """Request UI approval and return the operation result to the waiting tool.

    Intended for tools whose result must return to the model after approval.
    The caller should run this function on a worker thread, never the Qt thread.
    """
    completed = threading.Event()
    outcome: dict[str, object] = {}

    def _finished(accepted: bool, result: object) -> None:
        outcome["accepted"] = accepted
        outcome["result"] = result
        completed.set()

    response = request(key, title, detail, run, on_resolve=_finished)
    if not response.startswith("[CONFIRMATION_PENDING]"):
        return response
    if not completed.wait(max(1.0, float(wait_timeout))):
        return "Approval or the approved operation timed out. Check the JARVIS activity log before retrying."
    if not outcome.get("accepted"):
        return outcome.get("result") or "The user declined the operation; nothing was done."
    return outcome.get("result") or "The approved operation finished without a result."


def resolve(accepted: bool) -> None:
    """Called by the UI when the user presses CONFIRM or CANCEL.

    Runs the stored callable on a worker thread — this is invoked from the Qt
    thread, and shutting the machine down from inside a button handler would
    freeze the interface on its way out."""
    global _pending

    with _lock:
        p, _pending = _pending, None

    if _hide_cb:
        try:
            _hide_cb()
        except Exception:
            pass

    if p is None:
        return

    if time.monotonic() - p.at > TIMEOUT_SECONDS:
        _log(f"SYS: Confirmation expired — {p.title}")
        if p.on_resolve:
            try:
                p.on_resolve(False, "The confirmation expired; nothing was done.")
            except Exception:
                pass
        return

    if not accepted:
        _log(f"SYS: Cancelled — {p.title}")
        if p.on_resolve:
            try:
                p.on_resolve(False, "The user cancelled the operation; nothing was done.")
            except Exception:
                pass
        return

    def _worker():
        try:
            result = p.run() or "Done."
            if p.on_resolve:
                p.on_resolve(True, result)
            _log(f"SYS: Confirmed — {p.title}. {result}")
        except Exception as e:
            if p.on_resolve:
                try:
                    p.on_resolve(True, f"The operation failed: {e}")
                except Exception:
                    pass
            _log(f"ERR: {p.title} failed — {e}")

    threading.Thread(target=_worker, daemon=True, name=f"confirm-{p.key}").start()


def pending_title() -> str:
    """'' when nothing is waiting. Lets an action avoid stacking two banners."""
    with _lock:
        if _pending is None:
            return ""
        if time.monotonic() - _pending.at > TIMEOUT_SECONDS:
            return ""
        return _pending.title
