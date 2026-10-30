"""Behavioral lessons JARVIS learns from its own mistakes.

``memory_manager.py`` answers "what is true about this person".  This module
answers a different question: "what should I do differently next time?"  Mixing
the two would be a mistake — a stored preference must never be silently
overwritten by an automatically inferred rule, and a failed tool call is not a
fact about the owner.

The store is deliberately small and local:

* a tool that raises (or returns a known failure shape) is recorded with the
  arguments that failed, so the next attempt can start from evidence instead of
  repeating the same call;
* a successful retry resolves the failure and, when the arguments changed,
  records the change that worked;
* an owner correction ("не то", "я же просил", "I meant…") is stored as a
  behavioural rule, because the owner has just told us what the right behaviour
  is;
* at the end of a session a short reflection pass may distil up to three
  reusable lessons from the transcript.

Everything is bounded (``_MAX_LESSONS``), deduplicated by a signature, and
redacted before it touches disk.  Lessons are injected into the system prompt at
session start and into the tool result right after a failure, so the model can
act on them in the same conversation as well as in future ones.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

# Same base-directory rule as memory_manager: a frozen build keeps its memory
# beside the executable, a source checkout keeps it in the project.
def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
LESSONS_PATH = BASE_DIR / "memory" / "lessons.json"

_lock = threading.RLock()
_MAX_LESSONS = 120
_MAX_TEXT = 500
_MAX_FIX = 600
_PROMPT_MAX_CHARS = 950
_PROMPT_LIMIT = 8
_RESOLVE_WINDOW_SECONDS = 6 * 3600

_KINDS = {
    "tool_failure",
    "unknown_tool",
    "correction",
    "reflection",
    "explicit",
}

# Tool names are identifiers. A reflection is allowed to return "general", but
# never an arbitrary sentence in the scope field.
_SCOPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

_SECRET_PATTERNS = (
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "[redacted-key]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[redacted-key]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[redacted-token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[redacted-token]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[redacted-token]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._-]{12,}"), r"\1 [redacted]"),
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key)\b\s*[:=]\s*\S+"
        ),
        r"\1=[redacted]",
    ),
)

_SENSITIVE_KEY_RE = re.compile(
    r"(?:pass|pwd|secret|token|key|credential|cookie|auth|session)", re.I
)

_TRANSIENT_RE = re.compile(
    r"(?:timed?\s*out|timeout|connection\s+(?:reset|refused|closed)|"
    r"temporary|try again|rate\s*limit|quota|429|503|unavailable|"
    r"network\s+error|таймаут|временн|повторит|не\s+удалось\s+(?:подключ|соедин))",
    re.I,
)

# Strong markers are phrases that virtually only appear when the owner is
# correcting the assistant.  Weak markers ("wrong", "неправильно") are useful
# too, but a question such as "что неправильно в этом коде?" must not be stored
# as a correction, so weak markers are ignored in question-shaped messages.
_STRONG_CORRECTION_PATTERNS = (
    re.compile(r"^\s*не\s+то\s*[.!]?\s*$", re.I),
    re.compile(r"^\s*не\s+так\s*[.!]?\s*$", re.I),
    re.compile(r"\b(?:это|ты|вы|так|всё|все)\s+(?:совсем\s+)?не\s+то\b", re.I),
    re.compile(
        r"\bне\s+то\s+(?:сделал|сделала|открыл|открыла|сказал|сказала|"
        r"запустил|запустила|нажал|нажала)\b",
        re.I,
    ),
    re.compile(
        r"\bя\s+(?:же\s+)?(?:просил|просила|говорил|говорила|имел|имела)\b",
        re.I,
    ),
    re.compile(
        r"\bя\s+(?:же\s+)?не\s+это\s+(?:просил|просила|говорил|говорила)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:ты|вы)\s+(?:ошиб(?:ся|лась|аешься)|не\s+понял|не\s+поняла)\b",
        re.I,
    ),
    re.compile(r"\b(?:это|так|всё|все)\s+неправильно\b", re.I),
    re.compile(r"\b(?:нет|стоп)[,!.]?\s+не\s+так\b", re.I),
    re.compile(
        r"\b(?:нет|стоп)[,!.]?\s+я\s+(?:же\s+)?(?:просил|говорил|имел)\b",
        re.I,
    ),
    re.compile(r"\bthat(?:'s| is) not what i\b", re.I),
    re.compile(r"\bi (?:meant|said|asked|wanted)\b", re.I),
    re.compile(r"\bno[,!.]? i (?:said|meant|asked|wanted)\b", re.I),
    re.compile(r"\byou(?:'re| are) wrong\b", re.I),
    re.compile(r"\b(?:that|this) (?:is|'s) not (?:right|correct)\b", re.I),
    re.compile(r"\bnot what i (?:asked|meant|said)\b", re.I),
    re.compile(r"\byou misunderstood\b", re.I),
)

_WEAK_CORRECTION_PATTERNS = (
    re.compile(r"\bнеправильн\w*", re.I),
    re.compile(r"\bincorrect\b", re.I),
    re.compile(r"\bwrong\b", re.I),
)

_QUESTION_START_RE = re.compile(
    r"^\s*(?:что|чем|почему|зачем|как|где|когда|кто|"
    r"what|why|how|where|when|who|which)\b",
    re.I,
)

_MISTAKE_HINT_RE = re.compile(
    r"\b(?:failed|error|mistake|wrong|incorrect|ошиб\w*|не\s+то|не\s+так|"
    r"неправильн\w*|не\s+получилось|не\s+сработал\w*|сломал\w*)",
    re.I,
)


# ── small helpers ─────────────────────────────────────────────────────────────


def _now() -> tuple[float, str]:
    ts = time.time()
    return ts, datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _clean(value, limit: int = _MAX_TEXT) -> str:
    """Collapse whitespace, strip control bytes, redact obvious secrets."""
    text = str(value or "")
    text = "".join(ch if ch >= " " or ch in "\t\n" else " " for ch in text)
    text = re.sub(r"\s+", " ", text).strip()
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _norm(value) -> str:
    """Normalised text for signatures; punctuation and case do not matter."""
    text = _clean(value, 2000).lower()
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _error_signature(error: str) -> str:
    """Stable-enough fingerprint of an error.

    A traceback or a path often contains the thing that changes between two
    occurrences of the same bug (line number, file name, PID).  Replacing those
    with placeholders is what lets the second failure find the first lesson
    instead of creating a near-duplicate.
    """
    text = _norm(error)[:300]
    text = re.sub(r"[a-z]:\\[^\s]+", "<path>", text)
    text = re.sub(r"[a-z0-9_.-]+/[a-z0-9_./-]+", "<path>", text)
    text = re.sub(r"\b[0-9a-f]{8,}\b", "#", text)
    text = re.sub(r"\b\d+\b", "#", text)
    return text[:240]


def _signature(kind: str, scope: str, trigger: str, mistake: str, fix: str) -> str:
    if kind in {"tool_failure", "unknown_tool"}:
        return f"{kind}|{scope}|{_error_signature(mistake)}"
    if kind == "correction":
        return f"correction|{_norm(fix)[:240]}"
    if kind == "explicit":
        return f"explicit|{_norm(fix)[:240]}"
    return f"{kind}|{scope}|{_norm(trigger)[:120]}|{_norm(fix)[:240]}"


def _sanitize_args(args) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    parts: list[str] = []
    for key in sorted(args, key=lambda item: str(item))[:8]:
        name = _clean(key, 40)
        value = args[key]
        if isinstance(value, (dict, list, tuple)):
            value = f"<{type(value).__name__}:{len(value)}>"
        elif isinstance(value, bool):
            value = "true" if value else "false"
        else:
            value = _clean(value, 90)
        if _SENSITIVE_KEY_RE.search(name):
            value = "<redacted>"
        parts.append(f"{name}={value}")
    return _clean(", ".join(parts), 300)


def _action_key(args) -> str:
    """The action/op a multi-operation tool was asked to perform, if any."""
    if not isinstance(args, dict):
        return ""
    return _clean(args.get("action") or args.get("op") or "", 40).lower()


def _is_transient(error: str) -> bool:
    return bool(_TRANSIENT_RE.search(str(error or "")))


# ── persistence ───────────────────────────────────────────────────────────────


def _empty() -> dict:
    return {"version": 1, "lessons": []}


def _load_unlocked() -> dict:
    if not LESSONS_PATH.exists():
        return _empty()
    try:
        data = json.loads(LESSONS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a corrupt store must not break JARVIS
        print(f"[Learning] ⚠️ Could not read lessons: {exc}")
        return _empty()
    if isinstance(data, list):  # tolerate an early hand-written store
        return {"version": 1, "lessons": [item for item in data if isinstance(item, dict)]}
    if not isinstance(data, dict):
        return _empty()
    items = data.get("lessons")
    if not isinstance(items, list):
        items = []
    data["lessons"] = [item for item in items if isinstance(item, dict)]
    data.setdefault("version", 1)
    return data


def _save_unlocked(data: dict) -> None:
    LESSONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    tmp = LESSONS_PATH.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    # os.replace is atomic, so a crash mid-write cannot leave an empty file
    # where the lessons used to be.
    tmp.replace(LESSONS_PATH)


def load_lessons() -> list[dict]:
    with _lock:
        return list(_load_unlocked().get("lessons", []))


def _prune(items: list[dict]) -> list[dict]:
    """Keep the store bounded without throwing away the rules that matter."""
    if len(items) <= _MAX_LESSONS:
        return items

    def priority(item: dict) -> tuple:
        owner = 2 if item.get("source") == "owner" or item.get("kind") == "explicit" else 0
        unresolved = 1 if item.get("status") in {"open", "active"} else 0
        return (
            owner,
            unresolved,
            float(item.get("confidence") or 0.0),
            min(int(item.get("hits") or 1), 20),
            float(item.get("updated_ts") or 0.0),
        )

    kept = sorted(items, key=priority, reverse=True)[:_MAX_LESSONS]
    # JSON stays readable in chronological order even though pruning is by value.
    kept.sort(key=lambda item: float(item.get("created_ts") or 0.0))
    return kept


# ── recording ─────────────────────────────────────────────────────────────────


def record(
    *,
    kind: str,
    scope: str = "general",
    trigger: str = "",
    mistake: str = "",
    fix: str = "",
    evidence: str = "",
    source: str = "auto",
    confidence: float = 0.6,
    signature: str = "",
    status: str = "active",
    transient: bool = False,
    action_key: str = "",
    args_hint: str = "",
) -> dict | None:
    """Insert or update one lesson. Returns the stored record, or None.

    Deduplication is by ``signature``.  A repeated failure increments ``hits``
    and raises confidence a little; it does not create a second near-identical
    line that would crowd the prompt.
    """
    kind = str(kind or "").strip().lower()
    if kind not in _KINDS:
        return None
    scope = _clean(scope, 64) or "general"
    if scope != "general" and not _SCOPE_RE.match(scope):
        scope = "general"
    trigger = _clean(trigger, 240)
    mistake = _clean(mistake, _MAX_TEXT)
    fix = _clean(fix, _MAX_FIX)
    evidence = _clean(evidence, _MAX_TEXT)
    source = _clean(source, 20) or "auto"
    action_key = _clean(action_key, 40).lower()
    args_hint = _clean(args_hint, 300)
    if not mistake and not fix:
        return None
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.6

    sig = signature or _signature(kind, scope, trigger, mistake, fix)
    ts, iso = _now()

    with _lock:
        data = _load_unlocked()
        items = data["lessons"]
        existing = next((item for item in items if item.get("signature") == sig), None)
        if existing is not None:
            existing["hits"] = int(existing.get("hits") or 1) + 1
            existing["updated_ts"] = ts
            existing["updated"] = iso
            existing["last_seen"] = iso
            if fix and (
                not existing.get("fix")
                or source == "owner"
                or existing.get("source") not in {"owner", "explicit"}
            ):
                existing["fix"] = fix
            if trigger and not existing.get("trigger"):
                existing["trigger"] = trigger
            if mistake and not existing.get("mistake"):
                existing["mistake"] = mistake
            if evidence:
                existing["evidence"] = evidence
            if args_hint:
                existing["args_hint"] = args_hint
            if action_key:
                existing["action_key"] = action_key
            if status == "open":
                existing["status"] = "open"
                existing["resolved_at"] = ""
                existing["resolved_ts"] = 0.0
                # A recovery that no longer recovers is not a fix. An owner
                # rule is never overwritten by an automatic failure record.
                if existing.get("source") not in {"owner", "explicit"}:
                    existing["fix"] = fix
            elif existing.get("status") not in {"open"}:
                existing["status"] = status
            existing["transient"] = bool(existing.get("transient") or transient)
            existing["confidence"] = min(
                0.97, max(float(existing.get("confidence") or 0.0), confidence) + 0.03
            )
            item = existing
        else:
            item = {
                "id": uuid.uuid4().hex[:12],
                "kind": kind,
                "scope": scope,
                "trigger": trigger,
                "mistake": mistake,
                "fix": fix,
                "evidence": evidence,
                "signature": sig,
                "source": source,
                "confidence": confidence,
                "hits": 1,
                "status": status,
                "transient": bool(transient),
                "action_key": action_key,
                "args_hint": args_hint,
                "created": iso,
                "created_ts": ts,
                "updated": iso,
                "updated_ts": ts,
                "last_seen": iso,
                "resolved_at": "",
                "resolved_ts": 0.0,
            }
            items.append(item)
        data["lessons"] = _prune(items)
        _save_unlocked(data)
    return item


def record_tool_failure(
    tool: str,
    error: str,
    args: dict | None = None,
    request: str = "",
    *,
    unknown: bool = False,
) -> dict | None:
    """Remember a failed tool call well enough to avoid repeating it blindly."""
    tool = _clean(tool, 64) or "unknown"
    error = _clean(error, _MAX_TEXT)
    if not error:
        return None
    transient = _is_transient(error)
    if unknown:
        fix = "This tool does not exist. Use only tool names declared in the current list."
        confidence = 0.8
    elif transient:
        fix = "Transient failure: retry once after a pause, then report it instead of looping."
        confidence = 0.3
    else:
        fix = ""
        confidence = 0.55
    # Count how many open failures already exist for this tool scope — even when
    # the error text differs (different signatures), a second failure of the same
    # tool is a strong signal that the call must not be retried blindly.
    prior = 0
    with _lock:
        prior = sum(
            1
            for item in _load_unlocked().get("lessons", [])
            if item.get("scope") == tool
            and item.get("kind") in {"tool_failure", "unknown_tool"}
            and item.get("status") == "open"
        )
    item = record(
        kind="unknown_tool" if unknown else "tool_failure",
        scope=tool,
        trigger=_clean(request, 240),
        mistake=error,
        fix=fix,
        evidence=error,
        source="auto",
        confidence=confidence,
        status="open",
        transient=transient,
        action_key=_action_key(args),
        args_hint=_sanitize_args(args),
    )
    if item is not None and prior:
        item["hits"] = int(item.get("hits") or 1) + prior
    return item


def record_tool_success(tool: str, args: dict | None = None) -> dict | None:
    """Resolve the latest open failure for this tool and learn what changed."""
    tool = _clean(tool, 64)
    if not tool:
        return None
    ts, iso = _now()
    with _lock:
        data = _load_unlocked()
        items = data["lessons"]
        open_items = [
            item
            for item in items
            if item.get("scope") == tool
            and item.get("kind") in {"tool_failure", "unknown_tool"}
            and item.get("status") == "open"
        ]
        if not open_items:
            return None
        success_action = _action_key(args)
        if success_action:
            matching = [
                item for item in open_items if item.get("action_key") == success_action
            ]
            if matching:
                open_items = matching
            else:
                no_action = [
                    item for item in open_items if not item.get("action_key")
                ]
                if no_action:
                    open_items = no_action
                else:
                    return None
        else:
            no_action = [
                item for item in open_items if not item.get("action_key")
            ]
            if no_action:
                open_items = no_action
            # If every failure carried an action and the success did not, we
            # cannot tell which one it resolves, so learn nothing rather than
            # marking a possibly unrelated failure as fixed.
            else:
                return None
        item = max(open_items, key=lambda entry: float(entry.get("updated_ts") or 0.0))
        last_seen_ts = float(item.get("updated_ts") or 0.0)
        item["status"] = "resolved"
        item["resolved_at"] = iso
        item["resolved_ts"] = ts
        item["updated"] = iso
        item["updated_ts"] = ts
        item["last_seen"] = iso
        if not item.get("fix"):
            # Only learn "this argument change was the fix" when the success
            # follows the failure closely. A success hours later may be a
            # different operation that happened to use the same tool.
            fresh = (ts - last_seen_ts) <= _RESOLVE_WINDOW_SECONDS
            args_hint = _sanitize_args(args) if fresh else ""
            if item.get("transient"):
                item["fix"] = "Retrying later succeeded, so this failure was transient."
            elif args_hint and args_hint != item.get("args_hint"):
                item["fix"] = f"A later call with different arguments succeeded: {args_hint}."
            elif args_hint:
                item["fix"] = f"A later call succeeded with: {args_hint}."
            else:
                item["fix"] = "A later retry succeeded."
            item["confidence"] = max(float(item.get("confidence") or 0.0), 0.7)
        data["lessons"] = _prune(items)
        _save_unlocked(data)
    return item


def _looks_like_question(text: str) -> bool:
    return "?" in text and bool(_QUESTION_START_RE.match(text))


def looks_like_correction(text: str) -> bool:
    """True only when the owner is plausibly correcting JARVIS, not asking."""
    value = _clean(text, 1000)
    if not value:
        return False
    if any(pattern.search(value) for pattern in _STRONG_CORRECTION_PATTERNS):
        return True
    if _looks_like_question(value):
        return False
    return any(pattern.search(value) for pattern in _WEAK_CORRECTION_PATTERNS)


_CORRECTION_PREFIX_PATTERNS = (
    re.compile(r"^\s*(?:не\s+то|не\s+так|нет|стоп)[,!.\s]+", re.I),
    re.compile(
        r"^\s*я\s+(?:же\s+)?(?:просил[а]?|говорил[а]?|имел[а]?)\s+", re.I
    ),
    re.compile(
        r"^\s*я\s+(?:же\s+)?не\s+это\s+(?:просил[а]?|говорил[а]?)[,!.\s]*", re.I
    ),
    re.compile(
        r"^\s*(?:ты|вы)\s+(?:ошиб(?:ся|лась)|не\s+понял[а]?)[,!.\s]*", re.I
    ),
    re.compile(
        r"^\s*(?:no[,!.]?\s*)?i\s+(?:meant|said|asked(?:\s+for)?|wanted)\s+",
        re.I,
    ),
    re.compile(
        r"^\s*(?:that(?:'s| is)\s+)?not\s+what\s+i\s+(?:asked|meant|said)[,!.\s]*",
        re.I,
    ),
)


def _correction_rule(text: str) -> str:
    """Turn "не то, я просил калькулятор" into "калькулятор".

    The raw sentence is kept as evidence, but the reusable part is the request
    the owner actually wanted. A correction that is only "ты ошибся" has no
    extractable request and is stored whole.
    """
    rule = text
    for _ in range(3):
        before = rule
        for pattern in _CORRECTION_PREFIX_PATTERNS:
            rule = pattern.sub("", rule, count=1)
        if rule == before:
            break
    rule = rule.strip(" ,.!-—")
    return rule or text


def record_user_correction(
    user_text: str,
    previous_request: str = "",
    previous_reply: str = "",
) -> dict | None:
    """Store an owner correction as a reusable behavioural rule."""
    text = _clean(user_text, _MAX_TEXT)
    if not text or not looks_like_correction(text):
        return None
    trigger = _clean(previous_request, 240) or "the previous request"
    mistake = _clean(previous_reply, 320) or "the previous answer or action did not match"
    item = record(
        kind="correction",
        scope="general",
        trigger=trigger,
        mistake=mistake,
        fix=_correction_rule(text),
        evidence=text,
        source="owner",
        confidence=0.82,
    )
    return item


def remember_rule(rule: str, trigger: str = "", scope: str = "general") -> dict | None:
    """Store an explicitly requested rule, e.g. "never close my browser"."""
    rule = _clean(rule, _MAX_FIX)
    if len(rule) < 3:
        return None
    scope = _clean(scope, 64) or "general"
    if scope != "general" and not _SCOPE_RE.match(scope):
        scope = "general"
    return record(
        kind="explicit",
        scope=scope,
        trigger=_clean(trigger, 240),
        mistake="",
        fix=rule,
        evidence=rule,
        source="owner",
        confidence=0.95,
    )


# ── formatting for the model ──────────────────────────────────────────────────


def _score(item: dict, now: float) -> float:
    kind = str(item.get("kind") or "")
    base = {
        "explicit": 40.0,
        "correction": 30.0,
        "unknown_tool": 18.0,
        "tool_failure": 15.0,
        "reflection": 20.0,
    }.get(kind, 10.0)
    updated = float(item.get("updated_ts") or 0.0)
    age_days = max(0.0, (now - updated) / 86400.0)
    recency = max(0.0, 7.0 - age_days)
    status_bonus = 6.0 if item.get("status") in {"open", "active"} else 0.0
    return (
        base
        + float(item.get("confidence") or 0.0) * 20.0
        + min(int(item.get("hits") or 1), 5) * 4.0
        + recency
        + status_bonus
    )


def _select(limit: int, now: float, scope: str | None = None) -> list[dict]:
    selected: list[tuple[float, dict]] = []
    for item in load_lessons():
        if scope and item.get("scope") != scope:
            continue
        # A single transient timeout is noise.  Twice is a pattern.
        if item.get("transient") and int(item.get("hits") or 1) < 2:
            continue
        if item.get("status") == "resolved" and not item.get("fix"):
            continue
        selected.append((_score(item, now), item))
    selected.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score_value, item in selected[: max(1, limit)]]


def _render_item(item: dict) -> str:
    kind = str(item.get("kind") or "")
    scope = _clean(item.get("scope"), 64) or "general"
    trigger = _clean(item.get("trigger"), 110)
    mistake = _clean(item.get("mistake"), 150)
    fix = _clean(item.get("fix"), 220)

    if kind == "explicit":
        line = f"- Owner rule: {fix}"
        if trigger:
            line += f" (when: {trigger})"
        return line
    if kind == "correction":
        line = "- Owner correction"
        if trigger:
            line += f' after "{trigger}"'
        return line + f": {fix}"
    if kind == "unknown_tool":
        return f"- Tool `{scope}` does not exist. {fix or 'Use only declared tools.'}"
    if kind == "tool_failure":
        line = f"- `{scope}` previously failed: {mistake}"
        if fix:
            line += f" What works: {fix}"
        else:
            line += " Do not retry the identical call unchanged."
        return line
    line = f"- {fix or mistake}"
    if trigger:
        line += f" (when: {trigger})"
    return line


def format_lessons_for_prompt(limit: int = _PROMPT_LIMIT) -> str:
    """Build the compact lessons block for the system prompt."""
    now = time.time()
    items = _select(limit, now)
    if not items:
        return ""
    header = (
        "[LESSONS FROM YOUR OWN MISTAKES — apply silently, never recite this list]\n"
        "These were recorded from real failures or owner corrections. A current "
        "explicit instruction from the owner always wins over an old lesson."
    )
    lines = [header]
    used = len(header) + 1
    for item in items:
        line = _render_item(item)
        if not line:
            continue
        if used + len(line) + 1 > _PROMPT_MAX_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) + "\n"


def lessons_for_tool(tool: str, limit: int = 2, max_chars: int = 420) -> str:
    """Short block returned with a failed tool result, in the same turn."""
    tool = _clean(tool, 64)
    if not tool:
        return ""
    now = time.time()
    items = [
        item
        for item in _select(max(limit * 3, 4), now, scope=tool)
        if item.get("kind") in {"tool_failure", "unknown_tool", "explicit"}
    ][: max(1, limit)]
    if not items:
        return ""
    header = "[PAST LESSONS FOR THIS TOOL — internal note, do not read aloud]"
    lines = [header]
    used = len(header) + 1
    for item in items:
        line = _render_item(item)
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) + "\n"


# ── owner-facing queries ─────────────────────────────────────────────────────


def stats() -> dict:
    items = load_lessons()
    kinds: dict[str, int] = {}
    for item in items:
        kind = str(item.get("kind") or "other")
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "total": len(items),
        "open": sum(1 for item in items if item.get("status") == "open"),
        "resolved": sum(1 for item in items if item.get("status") == "resolved"),
        "owner": sum(
            1 for item in items if item.get("source") == "owner" or item.get("kind") == "explicit"
        ),
        "kinds": kinds,
        "last_updated": max(
            (str(item.get("updated") or "") for item in items), default=""
        ),
    }


def list_recent(limit: int = 10) -> list[dict]:
    try:
        count = max(1, min(30, int(limit)))
    except (TypeError, ValueError):
        count = 10
    items = sorted(
        load_lessons(),
        key=lambda item: float(item.get("updated_ts") or 0.0),
        reverse=True,
    )
    return [
        {
            "id": str(item.get("id") or ""),
            "kind": str(item.get("kind") or ""),
            "scope": str(item.get("scope") or "general"),
            "trigger": str(item.get("trigger") or ""),
            "mistake": str(item.get("mistake") or ""),
            "fix": str(item.get("fix") or ""),
            "status": str(item.get("status") or ""),
            "hits": int(item.get("hits") or 1),
            "confidence": float(item.get("confidence") or 0.0),
            "updated": str(item.get("updated") or ""),
        }
        for item in items[:count]
    ]


def forget(ref: str) -> int:
    """Remove lessons by id/prefix or by a text fragment. Returns count removed."""
    needle = _clean(ref, 200)
    if not needle:
        return 0
    with _lock:
        data = _load_unlocked()
        items = data["lessons"]
        # Only a hex-looking reference is treated as an id/prefix. Otherwise a
        # short ordinary word such as "a" would match the first random id and
        # delete the wrong lesson.
        exact = []
        if re.fullmatch(r"[0-9a-f]{4,12}", needle.lower()):
            exact = [
                item
                for item in items
                if str(item.get("id") or "").lower().startswith(needle.lower())
            ]
        if exact:
            removed = {id(item) for item in exact}
            data["lessons"] = [item for item in items if id(item) not in removed]
            _save_unlocked(data)
            return len(exact)
        query = _norm(needle)
        if not query:
            return 0
        matches = [
            item
            for item in items
            if query in _norm(item.get("fix"))
            or query in _norm(item.get("mistake"))
            or query in _norm(item.get("trigger"))
            or query in _norm(item.get("scope"))
        ]
        if not matches:
            return 0
        removed = {id(item) for item in matches}
        data["lessons"] = [item for item in items if id(item) not in removed]
        _save_unlocked(data)
        return len(matches)


def clear_auto() -> int:
    """Forget everything learned automatically; keep owner rules and corrections."""
    with _lock:
        data = _load_unlocked()
        items = data["lessons"]
        kept = [
            item
            for item in items
            if item.get("source") == "owner" or item.get("kind") == "explicit"
        ]
        removed = len(items) - len(kept)
        if removed:
            data["lessons"] = kept
            _save_unlocked(data)
        return removed


# ── end-of-session reflection ────────────────────────────────────────────────


def session_worth_reflecting(transcript: str) -> bool:
    """Cheap gate so an ordinary conversation does not cost a model call."""
    text = str(transcript or "")
    if not text.strip():
        return False
    for line in text.splitlines():
        if line.startswith("User:") and looks_like_correction(line[5:]):
            return True
    return bool(_MISTAKE_HINT_RE.search(text))


def extract_lessons_from_transcript(transcript: str, language: str = "") -> list[dict]:
    """Ask the fast model for up to three reusable lessons, then store them.

    This is best-effort: no API key, no quota, or a malformed answer simply
    returns an empty list.  A failed reflection must never break the shutdown
    path that saves the ordinary session summary.
    """
    transcript = str(transcript or "").strip()
    if not transcript or not session_worth_reflecting(transcript):
        return []
    try:
        from core import gemini
    except Exception:
        return []

    lang = _clean(language, 40) or "the language of the conversation"
    prompt = (
        "You are reviewing a transcript of JARVIS, a desktop voice assistant.\n"
        "Find only mistakes that should not be repeated. Return ONLY a JSON array "
        "with at most 3 objects, each shaped as:\n"
        '{"trigger": "short situation", "mistake": "what JARVIS did wrong", '
        '"fix": "what to do instead", "scope": "general or a tool name"}\n'
        "Rules:\n"
        "- Include a mistake only when the user explicitly corrected JARVIS or a "
        "tool action clearly failed and the transcript shows what went wrong.\n"
        "- Do not include ordinary preferences, facts, one-off requests, or things "
        "JARVIS did correctly.\n"
        "- The fix must be a reusable behavioural instruction, not a summary.\n"
        "- Do not include personal names, message contents, file paths, credentials, "
        "or other private data; generalise them.\n"
        "- If nothing qualifies, return [].\n"
        f"- Write trigger, mistake and fix in {lang}.\n\n"
        "TRANSCRIPT:\n" + transcript[-6000:]
    )
    try:
        data = gemini.as_json(
            prompt,
            tier=gemini.FAST,
            timeout_ms=25_000,
            default=[],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[Learning] ⚠️ Reflection failed: {exc}")
        return []
    if isinstance(data, dict):
        data = data.get("lessons") or data.get("items") or []
    if not isinstance(data, list):
        return []

    recorded: list[dict] = []
    for entry in data[:3]:
        if not isinstance(entry, dict):
            continue
        fix = _clean(entry.get("fix"), _MAX_FIX)
        mistake = _clean(entry.get("mistake"), _MAX_TEXT)
        trigger = _clean(entry.get("trigger"), 240)
        scope = _clean(entry.get("scope"), 64) or "general"
        if scope != "general" and not _SCOPE_RE.match(scope):
            scope = "general"
        if len(fix) < 3 or not mistake:
            continue
        item = record(
            kind="reflection",
            scope=scope,
            trigger=trigger,
            mistake=mistake,
            fix=fix,
            evidence=fix,
            source="reflection",
            confidence=0.64,
        )
        if item:
            recorded.append(item)
    if recorded:
        print(f"[Learning] 🧠 Learned {len(recorded)} lesson(s) from the session")
    return recorded


# Compatibility aliases for callers that use a different vocabulary. They all
# point at the same implementation, so there is exactly one store and one set of
# rules.
record_lesson = record
add_lesson = record
learn = record
learn_from_mistake = record
record_failure = record_tool_failure
record_success = record_tool_success
record_correction = record_user_correction
teach = remember_rule
lessons_for_prompt = format_lessons_for_prompt
get_lessons = load_lessons


__all__ = [
    "LESSONS_PATH",
    "clear_auto",
    "extract_lessons_from_transcript",
    "forget",
    "format_lessons_for_prompt",
    "lessons_for_tool",
    "list_recent",
    "load_lessons",
    "looks_like_correction",
    "record",
    "record_tool_failure",
    "record_tool_success",
    "record_user_correction",
    "remember_rule",
    "session_worth_reflecting",
    "stats",
    "record_lesson",
    "add_lesson",
    "learn",
    "learn_from_mistake",
    "record_failure",
    "record_success",
    "record_correction",
    "teach",
    "lessons_for_prompt",
    "get_lessons",
]
