"""
One place where the assistant's one-shot Gemini calls are made.

WHY THIS EXISTS
    The live conversation runs on the Live API and is not what this file is
    about. Everything else — reading a screenshot, parsing a flight page,
    turning a request into a shell command, working out which WhatsApp button
    answers a call — was a separate `genai.Client(...)` built at the point of
    use, with the model name written inline. Sixteen files did it, twenty-six
    times, and not one of them set a timeout.

    That is not tidiness, it is three real faults:

    NO TIMEOUT.  The SDK waits forever by default. `gemini-flash-latest` spent
    an afternoon returning 504 DEADLINE_EXCEEDED, and every one of those calls
    became an unbounded hang — measured at ten seconds of silence while a phone
    rang, and worse elsewhere, because nothing was there to give up.

    NO FALLBACK.  One hardcoded alias meant that when that alias was unwell,
    the feature was simply gone. A ladder costs nothing when the first rung
    works and saves the feature when it does not.

    NO SINGLE PLACE TO CHANGE.  A new model release meant editing sixteen files
    and hoping none were missed.

Model IDs are centralized below, with current stable Gemini Flash models
preferred and older releases excluded from the runtime fallback ladder.

"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.paths import CONFIG_DIR, LOGS_DIR, get_config_path, load_config

# ── Логирование Gemini на русском (консоль + файл) ───────────────────────
LOGS_DIR.mkdir(parents=True, exist_ok=True)
_GEMINI_LOG_FILE = LOGS_DIR / "jarvis_gemini.ru.log"

logger = logging.getLogger("jarvis.gemini")
logger.setLevel(logging.INFO)
logger.propagate = False  # не дублировать через корневой логгер

if not logger.handlers:
    _fmt = logging.Formatter("%(asctime)s [Gemini] %(message)s", datefmt="%d.%m.%Y %H:%M:%S")
    _fh = logging.FileHandler(_GEMINI_LOG_FILE, encoding="utf-8")
    _fh.setFormatter(_fmt)
    logger.addHandler(_fh)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)

# Task profiles, tried left to right. Keep model choice here so each task gets
# an appropriate model and a consistent fallback policy.
FAST = "fast"  # short classification, extraction, one-line decisions
SMART = "smart"  # everyday reasoning, documents, summaries
CODE = "code"  # code generation, review, and multi-step development
REPAIR = "repair"  # autonomous source repair; prioritize strongest reasoning
VISION = "vision"  # screenshots and other visual debugging
STRATEGY = "strategy"  # deep, infrequent campaign planning
SEARCH = "search"  # grounded search — REST only, see below

# Live is a last-resort one-shot rung for the separate Live quota. REST task
# models are preferred because they are faster for text/code work. Grounded
# search stays on REST because Live does not return grounding metadata.
LIVE = "live"

# Stable Google models, selected by task. Argon handles the hardest reasoning;
# 3.8 Flash is the everyday and long-horizon coding model; Flash-Lite handles
# quick, high-volume decisions.
_LADDERS = {
    FAST: (
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.6-flash",
        "gemini-3.8-flash",
        LIVE,
    ),
    SMART: (
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-4-argon",
        LIVE,
    ),
    CODE: (
        "gemini-4-argon",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        LIVE,
    ),
    REPAIR: (
        "gemini-4-argon",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        LIVE,
    ),
    VISION: (
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-4-argon",
        LIVE,
    ),
    STRATEGY: (
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-4-argon",
        LIVE,
    ),
    # Grounding metadata is required, so this route deliberately has no Live.
    SEARCH: (
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-4-argon",
    ),
}

TASK_TIERS = {
    "classification": FAST,
    "general": SMART,
    "coding": CODE,
    "repair": REPAIR,
    "vision": VISION,
    "game_strategy": STRATEGY,
    "search": SEARCH,
}
_TASK_LABELS = {
    "classification": "Быстрые решения",
    "general": "Обычные рассуждения и документы",
    "coding": "Написание и ревью кода",
    "repair": "Автопочинка JARVIS",
    "vision": "Разбор снимков и изображений",
    "game_strategy": "Стратегическое планирование в игре",
    "search": "Поиск с источниками",
}


def tier_for_task(task: str, default: str = SMART) -> str:
    """Return the shared model profile for a named task."""
    return TASK_TIERS.get(str(task or "").strip().lower(), default)


def models_for_task(task: str) -> tuple[str, ...]:
    """Return the model preference ladder used for a task."""
    return _LADDERS[tier_for_task(task)]


def describe_routes() -> str:
    """Describe the configured task router in a concise, user-facing form."""
    lines = [f"Диалог и голос: {live_model()}."]
    fallbacks = []
    if _groq_available():
        fallbacks.append("Groq (резерв)")
    if _github_available():
        fallbacks.append("GitHub (резерв)")
    for task, tier in TASK_TIERS.items():
        models = ["Gemini Live (резерв)" if model == LIVE else model
                  for model in _LADDERS[tier]]
        suffix = " → " + " → ".join(fallbacks) if fallbacks else ""
        lines.append(f"{_TASK_LABELS[task]}: " + " → ".join(models) + suffix + ".")
    return " ".join(lines)


def route_snapshot() -> dict:
    """Return the current task routes for the desktop status panel."""
    fallbacks = []
    if _groq_available():
        fallbacks.append("Groq (резерв)")
    if _github_available():
        fallbacks.append("GitHub (резерв)")
    tasks = []
    for task, tier in TASK_TIERS.items():
        chain = _LADDERS[tier]
        display = ["Gemini Live (резерв)" if model == LIVE else model
                   for model in chain]
        if fallbacks:
            display = display + fallbacks
        tasks.append({
            "task": task,
            "label": _TASK_LABELS[task],
            "models": display,
            "cooling": [name for model, name in zip(chain, display, strict=False)
                        if _cooling(model)],
        })
    return {"live_model": live_model(), "tasks": tasks}

# The conversation's own model, and ONE careful fallback behind it.
#
# Deliberately not a long ladder. The Live quota is not the one that runs out —
# the text models are — and Live models are not interchangeable the way text
# models are: they differ in which config fields they accept and in what they
# can do, so falling through a list of them risks connecting to something that
# behaves like a different assistant. The key also offers transcribe-live,
# live-translate and a robotics streaming model, none of which are assistants
# at all.
#
# The fallback stays in the same Live generation, with extended reasoning for
# cases where the regular low-latency model is unavailable.
LIVE_MODELS = (
    "models/gemini-3.8-live",
    "models/gemini-3.8-live-extended-thinking",
)

# The Live model to use for one-shot calls. main.py owns the real one; this is
# only the fallback for when this module is imported without it (tests).
_LIVE_FALLBACK = "models/gemini-3.8-live"

# How many one-shot Live sessions may exist at once.
#
# THE USER'S CONVERSATION OUTRANKS EVERY SIDE CALL.
# Nothing here can reach the microphone or the speaker — main.py has exactly one
# `.receive()` and it is bound to its own session, and audio only reaches the
# speaker through that one loop — so a side call cannot answer the user or talk
# over the reply. Verified alongside a live main session: four side calls fired
# while it was connected, each got its own answer, and the main session replied
# correctly both before and after with no errors.
#
# What a side call CAN do is take up a concurrent-session slot. That is the one
# way it could hurt the conversation, so it is capped, and a call that cannot
# get a slot quickly does not queue behind the others — it falls to the REST
# rung, which is what the ladder is for.
# Three, from the measurement: four side calls plus the conversation ran
# together without complaint, so three leaves the conversation a slot in hand
# while still covering any burst this app actually produces — tool calls run one
# after another, and the screen agent's loop is sequential.
_LIVE_SLOTS = threading.BoundedSemaphore(3)
_LIVE_SLOT_WAIT = 3.0

_ONE_SHOT_SYSTEM = (
    "You are a data-processing function, not an assistant and not in a "
    "conversation. There is no person listening to you. Produce exactly the "
    "output the request asks for and nothing else: no greeting, no "
    "acknowledgement, no 'Understood', no explanation, no closing remark, no "
    "restating of the question. If the request asks for JSON, emit only the "
    "JSON. If it asks for code, emit only the code. If it asks for one word, "
    "emit that one word. Preserve the exact spelling, punctuation, capitals "
    "and whitespace of anything you are asked to copy or return."
)

# Milliseconds. Not a preference: the API rejects anything under ten seconds
# with "Minimum allowed deadline is 10s", so this is the tightest bound it will
# accept. Callers with a long job (a whole document, a big image) pass more.
DEFAULT_TIMEOUT_MS = 10_000
MIN_TIMEOUT_MS = 10_000
MAX_TIMEOUT_MS = 300_000
_TIMEOUT_PROFILES = frozenset({FAST, SMART, CODE, REPAIR, VISION, STRATEGY, SEARCH})


def timeout_ms_for(profile: str = "default", fallback_ms: int | None = None) -> int:
    """Resolve a bounded Gemini deadline from user config and safe defaults.

    ``gemini_request_timeout_ms`` in api_keys.json sets the shared default
    when a caller has not supplied a task-specific budget.
    ``gemini_timeouts_ms`` may override individual profiles (for example
    ``{"image": 90000}``). An explicit caller value is retained as the
    fallback so long-running jobs keep their existing budgets unless the user
    changes the shared setting.
    """
    name = str(profile or "default").strip().lower() or "default"
    # Per-profile floors. FAST keeps the API's ten-second minimum; the other
    # tiers default higher because their models do heavier work — Argon
    # reviewing a file or planning a campaign routinely needs more than ten
    # seconds, and the old flat default turned that into a deadline error the
    # caller had to remember to override every time. Explicit caller values
    # still win over every entry below.
    defaults = {
        "default": DEFAULT_TIMEOUT_MS,
        FAST: DEFAULT_TIMEOUT_MS,
        SMART: 25_000,
        CODE: 60_000,
        REPAIR: 60_000,
        VISION: 60_000,
        STRATEGY: 120_000,
        SEARCH: 25_000,
        "image": 90_000,
        "tts": 90_000,
        "live_connect": 30_000,
        "live_turn": 60_000,
    }
    if name not in defaults:
        name = "default"

    configured = None
    try:
        from memory.config_manager import load_api_keys

        settings = load_api_keys() or {}
        per_profile = settings.get("gemini_timeouts_ms")
        if isinstance(per_profile, dict):
            configured = per_profile.get(name)
        if configured is None and fallback_ms is None:
            configured = settings.get("gemini_request_timeout_ms")
            if configured is None and isinstance(per_profile, dict):
                configured = per_profile.get("default")
    except Exception:
        pass

    value = configured if configured is not None else fallback_ms
    if value is None or isinstance(value, bool):
        value = defaults[name]
    try:
        resolved = int(value)
    except (TypeError, ValueError, OverflowError):
        resolved = defaults[name]
    return min(MAX_TIMEOUT_MS, max(MIN_TIMEOUT_MS, resolved))

_key_lock = threading.Lock()
_cached_key: str | None = None
_cached_key_2: str | None = None
_cached_key_signature: tuple[str, int, int] | None = None

# A rung that answered 429 is out of quota, and on the free tier it will stay
# that way for a while. Retrying it on every single call is a wasted round trip
# in front of every request the assistant makes — measured on this key, the
# lite rung was 429ing continuously, so every call was paying for it before
# reaching the model that could actually answer. Remembering that for a few
# minutes turns the ladder from a cost into a saving.
_COOLDOWN_SECONDS = 300
# A model that does not exist, or that this key may not use, is not coming back
# in five minutes. Retrying it on every call is a wasted round trip in front of
# everything the assistant does, so it is set aside for the session instead.
_GONE_SECONDS = 6 * 60 * 60
# Overloaded or timing out. Usually passes, so a shorter rest than "gone" —
# but long enough that the fourteen-second wait is paid once, not repeatedly.
_UNAVAILABLE_SECONDS = 30 * 60
_cooldown: dict[str, float] = {}
_cool_lock = threading.Lock()

# Circuit breaker for the local Ollama server: after several consecutive
# failures it is skipped for a while instead of being retried on every call.
_ollama_cooldown_until: float = 0.0
_ollama_failures: int = 0
_ollama_lock = threading.Lock()


# ── Локальный учёт квоты Gemini (чтобы не слать битые запросы) ──────────────
_QUOTA_STATE_FILE = CONFIG_DIR / "quota_state.json"

# Дефолтные лимиты для бесплатного tier Gemini (запрос/минуту, запрос/сутки)
_DEFAULT_RPM = 15
_DEFAULT_RPD = 1500


def _key_fingerprint(key: str) -> str:
    """Короткий отпечаток ключа для хранения в состоянии квоты (без открытого ключа)."""
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class QuotaTracker:
    """Отслеживает количество запросов по каждому ключу, чтобы не слать
    вызовы, которые с высокой вероятностью получат 429 из-за исчерпания квоты."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            if _QUOTA_STATE_FILE.exists():
                self._state = json.loads(_QUOTA_STATE_FILE.read_text(encoding="utf-8"))
                if not isinstance(self._state, dict):
                    self._state = {}
            else:
                self._state = {}
        except Exception:
            self._state = {}

    def _save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _QUOTA_STATE_FILE.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    def _limits(self, fingerprint: str) -> tuple[int, int]:
        key_entry = self._state.get("keys", {}).get(fingerprint, {})
        custom = key_entry.get("limits", {})
        rpm = int(custom.get("rpm", _DEFAULT_RPM))
        rpd = int(custom.get("rpd", _DEFAULT_RPD))
        return rpm, rpd

    def _prune(self, key_entry: dict, now: float) -> None:
        """Удаляет старые таймстампы (старше суток)."""
        day_ago = now - 86400
        key_entry["requests"] = [t for t in key_entry.get("requests", []) if t > day_ago]

    def can_proceed(self, key: str) -> bool:
        """Возвращает True, если по оценке можно слать запрос на этот ключ."""
        if not key:
            return False
        fp = _key_fingerprint(key)
        with self._lock:
            now = time.monotonic()
            keys_state = self._state.setdefault("keys", {})
            entry = keys_state.get(fp)
            if entry is None:
                entry = {"requests": [], "last_429": 0.0}
                keys_state[fp] = entry
            self._prune(entry, now)
            rpm, rpd = self._limits(fp)
            requests = entry.get("requests", [])
            last_429 = entry.get("last_429", 0.0)
            # Если был 429 менее чем 5 минут назад — не слать.
            if now - last_429 < 300:
                return False
            # Если за последнюю минуту уже было >= rpm — не слать.
            minute_ago = now - 60
            recent = [t for t in requests if t > minute_ago]
            if len(recent) >= rpm:
                return False
            # Если за сутки уже было >= rpd — не слать.
            if len(requests) >= rpd:
                return False
            return True

    def record(self, key: str, success: bool, is_quota_error: bool = False) -> None:
        """Записывает результат вызова для учёта квоты."""
        if not key:
            return
        fp = _key_fingerprint(key)
        with self._lock:
            now = time.monotonic()
            keys_state = self._state.setdefault("keys", {})
            entry = keys_state.get(fp)
            if entry is None:
                entry = {"requests": [], "last_429": 0.0}
                keys_state[fp] = entry
            self._prune(entry, now)
            entry.setdefault("requests", []).append(now)
            if is_quota_error:
                entry["last_429"] = now
            self._save()

    def set_limits(self, key: str, rpm: int | None = None, rpd: int | None = None) -> None:
        fp = _key_fingerprint(key)
        with self._lock:
            keys_state = self._state.setdefault("keys", {})
            entry = keys_state.setdefault(fp, {"requests": [], "last_429": 0.0})
            limits = entry.setdefault("limits", {})
            if rpm is not None:
                limits["rpm"] = rpm
            if rpd is not None:
                limits["rpd"] = rpd
            self._save()


_quota = QuotaTracker()


def _cool(model: str, seconds: float = _COOLDOWN_SECONDS) -> None:
    with _cool_lock:
        _cooldown[model] = time.monotonic() + seconds


def _quota_cooldown(err: str) -> float:
    """A smarter rest after 429, read from the error itself.

    Google's 429 carries two facts the fixed five minutes threw away:
    ``retryDelay`` — how long a per-minute quota wants to be left alone
    (usually tens of seconds), and a ``PerDay`` metric name — the rung is out
    for the day, since the free tier resets at midnight Pacific. Resting until
    the delay instead of a blanket five minutes means a rung that was ready in
    twenty seconds comes back after twenty seconds, and a rung that would stay
    silent for hours is not probed again and again in front of every request.
    """
    m = re.search(r'retryDelay"?[:\s]*"?(\d+)(?:\.\d+)?s', err)
    if not m:
        m = re.search(r'retry in\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*([\d.]+)?\s*s',
                      err, re.I)
        if m:
            hours, minutes, seconds = (m.group(1) or 0), (m.group(2) or 0), \
                (m.group(3) or 0)
            delay = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            return min(12 * 3600.0, max(10.0, delay + 2.0))
    if m:
        return min(600.0, max(10.0, float(m.group(1)) + 2.0))
    if "PerDay" in err or "per day" in err.lower():
        try:
            from zoneinfo import ZoneInfo

            now = datetime.now(ZoneInfo("America/Los_Angeles"))
            nxt = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            return min(12 * 3600.0, max(300.0, (nxt - now).total_seconds() + 60.0))
        except Exception:
            return 30 * 60.0
    return _COOLDOWN_SECONDS


def _quota_rest_label(seconds: float) -> str:
    """Human-readable rest duration for the operator console (RU)."""
    minutes = seconds // 60
    if minutes >= 60:
        hours = minutes // 60
        mins = minutes % 60
        if mins == 0:
            return f"{hours:.0f} часов"
        return f"{hours:.0f} ч {mins:.0f} мин"
    if minutes > 0:
        return f"{minutes:.0f} минут"
    return f"{seconds:.0f} сек"


def is_quota_error(err: str) -> bool:
    low = str(err or "").lower()
    return (
        "429" in str(err or "")
        or "resource_exhausted" in low
        or "resource exhausted" in low
        or "quota" in low
        or "rate limit" in low
        or "rate_limit" in low
        or "too many requests" in low
    )


def is_unavailable_error(err: str) -> bool:
    """The model is up but not answering — overloaded, or a deadline expired.

    Worth its own case because of what it costs: a 504 from one of these took
    fourteen seconds to arrive. Retrying that on every call puts the wait in
    front of everything the assistant does, so a rung that times out is rested
    like an exhausted one — for less long, since it is usually passing.
    """
    low = err.lower()
    return (
        "503" in err
        or "504" in err
        or "unavailable" in low
        or "deadline_exceeded" in low
    )


def is_gone_error(err: str) -> bool:
    """The model is not there, or not ours to use — a different thing from busy."""
    low = err.lower()
    return (
        "404" in err
        or "not found" in low
        or "is not supported" in low
        or "permission" in low
        or "403" in err
    )


def live_model() -> str:
    """The Live model to open the conversation with: the first rung that is not
    resting. If every one is resting the ladder is used from the top anyway —
    refusing to connect at all is never the better answer."""
    try:
        cfg = load_config("api_keys.json", include_legacy=True)
        preferred = str(cfg.get("gemini_live_model") or "").strip()
    except Exception:
        preferred = ""
    ladder = ((preferred,) + tuple(m for m in LIVE_MODELS if m != preferred)) if preferred in LIVE_MODELS else LIVE_MODELS
    for m in ladder:
        if not _cooling(m):
            return m
    return ladder[0]


def note_live_failure(model: str, err: str) -> bool:
    """Record why a Live model failed. True when it is worth trying the next.

    Only quota and availability move the ladder along. A bad API key or a
    dropped network is not the model's fault, and stepping down the ladder for
    those would work through every model and reach the same wall four times.
    """
    if is_quota_error(err):
        rest = _quota_cooldown(err)
        _cool(model, rest)
        logger.warning(f"модель Live {model} превысила квоту — пропуск на {_quota_rest_label(rest)}.")
        return True
    if is_gone_error(err):
        _cool(model, _GONE_SECONDS)
        logger.warning(f"модель Live {model} недоступна для этого ключа — откладываем в сторону.")
        return True
    return False


def _cooling(model: str) -> bool:
    with _cool_lock:
        until = _cooldown.get(model, 0.0)
        if until and time.monotonic() < until:
            return True
        _cooldown.pop(model, None)
        return False


def api_key(refresh: bool = False, key_num: int = 1) -> str:
    """The Gemini key from config/api_keys.json. Cached; never raises.

    key_num selects which configured key is returned: 1 is the primary,
    2 is the fallback key configured for quota exhaustion.
    """
    global _cached_key, _cached_key_2, _cached_key_signature
    with _key_lock:
        path = get_config_path("api_keys.json")
        try:
            stat = path.stat()
            signature = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = (str(path.resolve()), 0, 0)
        if (
            _cached_key is not None
            and not refresh
            and signature == _cached_key_signature
        ):
            return _cached_key_2 if key_num == 2 else _cached_key
        try:
            from core.secrets import get_secret

            secret = get_secret("gemini_api_key")
            secret_2 = get_secret("gemini_api_key_2")
        except Exception:  # pragma: no cover
            secret = secret_2 = None
        if secret:
            _cached_key = secret
        else:
            _cached_key = str(load_config("api_keys.json").get("gemini_api_key") or "")
        if secret_2:
            _cached_key_2 = secret_2
        else:
            _cached_key_2 = str(
                load_config("api_keys.json").get("gemini_api_key_2") or ""
            )
        _cached_key_signature = signature
        return _cached_key_2 if key_num == 2 else _cached_key


def client(
    timeout_ms: int | None = None,
    key: str = "",
    profile: str = "default",
):
    """A configured genai.Client with a deadline on it. Raises if there is no
    key, because a caller that cannot work without one should say so."""
    from google import genai
    from google.genai import types as gtypes

    key = key or api_key()
    if not key:
        raise RuntimeError("no Gemini API key is configured")
    timeout_ms = timeout_ms_for(profile, timeout_ms)
    return genai.Client(
        api_key=key,
        http_options=gtypes.HttpOptions(timeout=timeout_ms),
    )


class _Reply:
    """What a Live turn hands back, shaped like the REST response's `.text` so
    every existing call site keeps working unchanged."""

    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


def _live_model() -> str:
    """Whatever main.py is running, so upgrading the assistant upgrades this."""
    return getattr(sys.modules.get("main"), "LIVE_MODEL", None) or _LIVE_FALLBACK


def _to_live_parts(contents) -> list:
    """REST `contents` -> Live `parts`. Accepts a bare string, a list of
    strings, and the SDK's Part objects (which is how every image is passed
    here), because those are the three shapes the call sites actually use."""
    import base64

    items = contents if isinstance(contents, (list, tuple)) else [contents]
    parts = []
    for item in items:
        if isinstance(item, str):
            parts.append({"text": item})
            continue
        blob = getattr(item, "inline_data", None)
        if blob is not None:
            data = getattr(blob, "data", None)
            mime = getattr(blob, "mime_type", None) or "application/octet-stream"
            if isinstance(data, bytes):
                data = base64.b64encode(data).decode("ascii")
            parts.append({"inline_data": {"mime_type": mime, "data": data}})
            continue
        txt = getattr(item, "text", None)
        if txt:
            parts.append({"text": txt})
            continue
        if isinstance(item, dict):
            parts.append(item)
    return parts


async def _live_turn(
    parts: list,
    system: str,
    key: str,
    timeout_s: float,
    connect_timeout_s: float,
) -> str:
    from google import genai
    from google.genai import types as gtypes

    # Live uses a WebSocket, so its connect and turn deadlines are enforced
    # around the async operations below rather than relying on HttpOptions.
    cl = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
    # Silence the persona, or it answers instead of complying.
    #
    # These are conversational models and they behave like it: asked "Reply with
    # one word: ok" a Live turn came back with "Understood." — it treated the
    # instruction as something to acknowledge rather than something to do. The
    # REST models do not, because nobody ever taught them to be in a
    # conversation. Every call through this module wants a value, not a reply,
    # so the session is told what it is before it is told what to do.
    kwargs = {
        "response_modalities": ["AUDIO"],
        "output_audio_transcription": {},
        "system_instruction": _ONE_SHOT_SYSTEM + (f"\n\n{system}" if system else ""),
    }

    cm = cl.aio.live.connect(
        model=_live_model(), config=gtypes.LiveConnectConfig(**kwargs)
    )
    session = await asyncio.wait_for(cm.__aenter__(), timeout=connect_timeout_s)
    try:
        await asyncio.wait_for(
            session.send_client_content(
                turns={"role": "user", "parts": parts}, turn_complete=True
            ),
            timeout=timeout_s,
        )
        chunks: list[str] = []

        async def drain():
            async for resp in session.receive():
                sc = getattr(resp, "server_content", None)
                if sc and sc.output_transcription and sc.output_transcription.text:
                    chunks.append(sc.output_transcription.text)

        await asyncio.wait_for(drain(), timeout=timeout_s)
        # The transcription can trail the audio turn by a beat; a short second
        # drain stops a reply being cut mid-token. chat_takeover learned this
        # the same way and for the same reason.
        try:
            await asyncio.wait_for(drain(), timeout=1.5)
        except asyncio.TimeoutError:
            pass
        return "".join(chunks).strip()
    finally:
        try:
            await asyncio.wait_for(cm.__aexit__(None, None, None), timeout=2.0)
        except Exception:
            pass


def _live_call(contents, config, timeout_ms: int, key: str):
    """One throwaway Live session, run on its own loop in its own thread.

    A dedicated thread rather than asyncio.run() on the caller's: these are
    invoked from plugin executor threads, from UI worker threads and from
    main.py's own event loop, and asyncio.run() inside a thread that already has
    a running loop raises. Its own thread has no loop to collide with, wherever
    it was called from.

    Reconnecting costs 0.24s, so nothing is kept alive between calls — no
    session lifetime cap to manage, no GoAway to handle, no shared state.
    """
    system = ""
    if config is not None:
        system = (
            getattr(config, "system_instruction", None)
            or (config.get("system_instruction") if isinstance(config, dict) else "")
            or ""
        )

    parts = _to_live_parts(contents)
    if not parts:
        return None

    if not _LIVE_SLOTS.acquire(timeout=_LIVE_SLOT_WAIT):
        # Every slot is busy. Do not wait it out: falling to REST costs less
        # than holding a session the user's conversation might want.
        raise RuntimeError("no free Live slot — leaving them for the conversation")

    box: dict = {}

    def runner():
        try:
            box["text"] = asyncio.run(
                _live_turn(
                    parts,
                    str(system),
                    key,
                    max(10.0, timeout_ms / 1000.0),
                    timeout_ms_for("live_connect") / 1000.0,
                )
            )
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    try:
        th = threading.Thread(target=runner, daemon=True, name="gemini-live-oneshot")
        th.start()
        th.join(
            timeout=max(
                15.0,
                timeout_ms_for("live_connect") / 1000.0
                + 2 * max(10.0, timeout_ms / 1000.0)
                + 5.0,
            )
        )
    except BaseException:
        _LIVE_SLOTS.release()
        raise

    if th.is_alive():
        # join() истёк, а поток всё ещё держит Live-сессию. Слот отдавать
        # НЕЛЬЗЯ — иначе число реально открытых сессий превысит лимит, и
        # осиротевший поток продолжит жечь квоту уже «за пределами» слотов.
        # Слот освободит watcher, когда поток наконец умрёт.
        def _release_when_dead() -> None:
            th.join()
            _LIVE_SLOTS.release()

        threading.Thread(
            target=_release_when_dead, daemon=True, name="gemini-live-reaper"
        ).start()
        # Пустой ответ, не ошибка: таймаут ожидания — это ещё не сбой,
        # вызывающий упадёт на REST-лестнице и продолжит работу.
        return None

    _LIVE_SLOTS.release()
    if "error" in box:
        raise box["error"]
    text = box.get("text")
    return _Reply(text) if text else None


_gqt_lock = threading.Lock()
_groq_cooldown_until: float = 0.0
_gqt_failures: int = 0


def groq_key() -> str:
    """The Groq API key from config/env. Cached briefly, never raises."""
    try:
        from memory.config_manager import load_api_keys

        cfg = load_api_keys() or {}
    except Exception:
        cfg = {}
    return str(
        cfg.get("groq_api_key")
        or os.environ.get("GROQ_API_KEY", "")
        or ""
    ).strip()


def github_key() -> str:
    """The GitHub token from config/env (fallback for inference/models)."""
    try:
        from memory.config_manager import load_api_keys

        cfg = load_api_keys() or {}
    except Exception:
        cfg = {}
    return str(
        cfg.get("github_api_key")
        or os.environ.get("GITHUB_TOKEN", "")
        or ""
    ).strip()


def _groq_available() -> bool:
    """True when a Groq key is configured."""
    return bool(groq_key())


def _groq_call(contents, config=None, timeout_ms: int | None = None):
    """Run one generation through the Groq API as a fallback provider."""
    key = groq_key()
    if not key:
        return None

    if config is not None and getattr(config, "tools", None):
        return None

    import base64 as _b64

    items = contents if isinstance(contents, (list, tuple)) else [contents]
    text_parts: list[str] = []
    images: list[str] = []
    for item in items:
        if isinstance(item, str):
            text_parts.append(item)
            continue
        blob = getattr(item, "inline_data", None)
        if blob is not None:
            data = getattr(blob, "data", None)
            if isinstance(data, (bytes, bytearray)):
                images.append(_b64.b64encode(bytes(data)).decode("ascii"))
            continue
        txt = getattr(item, "text", None)
        if txt:
            text_parts.append(str(txt))
        elif isinstance(item, dict):
            if item.get("role") == "user":
                tp = item.get("parts", {}).get("text", "")
                if tp:
                    text_parts.append(str(tp))
            elif "text" in item:
                text_parts.append(str(item["text"]))
    prompt = "\n".join(part for part in text_parts if part.strip())
    if not prompt.strip() and not images:
        return None

    system = ""
    if config is not None:
        system = (
            getattr(config, "system_instruction", None)
            or (config.get("system_instruction") if isinstance(config, dict) else "")
            or ""
        )

    messages = []
    if system.strip():
        messages.append({"role": "system", "content": system.strip()})
    if images:
        content: list = [{"type": "text", "text": prompt}] if prompt.strip() else []
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": prompt})

    timeout = max(10.0, (timeout_ms or 60_000) / 1000.0)
    try:
        from groq import Groq

        client = Groq(api_key=key, timeout=timeout)
        resp = client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=messages,
        )
        text = str(resp.choices[0].message.content or "").strip()
        if not text:
            return None
        return _Reply(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"groq: {type(exc).__name__}: {str(exc)[:140]}")
        with _gqt_lock:
            _gqt_failures += 1
            if _gqt_failures >= 3:
                _groq_cooldown_until = time.monotonic() + 300.0
                _gqt_failures = 0
        return None


def _github_available() -> bool:
    """True when a GitHub token is configured."""
    return bool(github_key())


def _github_call(contents, config=None, timeout_ms: int | None = None):
    """Run one generation through GitHub Models as a fallback provider."""
    key = github_key()
    if not key:
        return None

    if config is not None and getattr(config, "tools", None):
        return None

    import base64 as _b64

    items = contents if isinstance(contents, (list, tuple)) else [contents]
    text_parts: list[str] = []
    images: list[str] = []
    for item in items:
        if isinstance(item, str):
            text_parts.append(item)
            continue
        blob = getattr(item, "inline_data", None)
        if blob is not None:
            data = getattr(blob, "data", None)
            if isinstance(data, (bytes, bytearray)):
                images.append(_b64.b64encode(bytes(data)).decode("ascii"))
            continue
        txt = getattr(item, "text", None)
        if txt:
            text_parts.append(str(txt))
        elif isinstance(item, dict):
            if item.get("role") == "user":
                tp = item.get("parts", {}).get("text", "")
                if tp:
                    text_parts.append(str(tp))
            elif "text" in item:
                text_parts.append(str(item["text"]))
    prompt = "\n".join(part for part in text_parts if part.strip())
    if not prompt.strip() and not images:
        return None

    system = ""
    if config is not None:
        system = (
            getattr(config, "system_instruction", None)
            or (config.get("system_instruction") if isinstance(config, dict) else "")
            or ""
        )

    messages = []
    if system.strip():
        messages.append({"role": "system", "content": system.strip()})
    if images:
        content: list = [{"type": "text", "text": prompt}] if prompt.strip() else []
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": prompt})

    timeout = max(10.0, (timeout_ms or 60_000) / 1000.0)
    try:
        from openai import OpenAI

        client = OpenAI(
            base_url="https://models.inference.ai.redhat.com",
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            timeout=timeout,
        )
        text = str(resp.choices[0].message.content or "").strip()
        if not text:
            return None
        return _Reply(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"github: {type(exc).__name__}: {str(exc)[:140]}")
        return None


def ollama_base_url() -> str:
    """Base URL of a local Ollama/OpenAI-compatible server, if configured."""
    try:
        from memory.config_manager import load_api_keys

        cfg = load_api_keys() or {}
    except Exception:
        cfg = {}
    return str(cfg.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")


def ollama_model() -> str:
    """Preferred local model for free, long-running work such as HOI4 planning."""
    try:
        from memory.config_manager import load_api_keys

        cfg = load_api_keys() or {}
    except Exception:
        cfg = {}
    model = str(cfg.get("ollama_model") or "").strip()
    return model or "qwen2.5-coder:14b"


def ollama_model_for(tier: str = FAST) -> str:
    """Pick a local model by task tier, falling back to the shared model."""
    try:
        from memory.config_manager import load_api_keys

        cfg = load_api_keys() or {}
    except Exception:
        cfg = {}
    if tier in (VISION,):
        model = str(cfg.get("ollama_model_vision") or "").strip()
    elif tier in (STRATEGY, REPAIR, CODE):
        model = str(cfg.get("ollama_model_strategy") or "").strip()
    else:
        model = str(cfg.get("ollama_model_fast") or "").strip()
    return model or ollama_model()


def ollama_keep_alive() -> str:
    try:
        from memory.config_manager import load_api_keys

        cfg = load_api_keys() or {}
    except Exception:
        cfg = {}
    return str(cfg.get("ollama_keep_alive") or "5m").strip() or "5m"


def ollama_available() -> bool:
    """True when a local Ollama server answers and a model is configured."""
    import urllib.request

    model = ollama_model()
    if not model:
        return False
    try:
        with urllib.request.urlopen(ollama_base_url() + "/api/tags", timeout=2):
            return True
    except Exception:
        return False


def ollama_vision_available() -> bool:
    """True when the configured local vision model is actually installed."""
    import json as _json
    import urllib.request

    if not ollama_available():
        return False
    try:
        with urllib.request.urlopen(ollama_base_url() + "/api/tags", timeout=2) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        names = {str(m.get("name", "")).strip() for m in data.get("models", [])}
        wanted = ollama_model_for(VISION)
        return wanted in names or any(n.split(":")[0] == wanted.split(":")[0] for n in names)
    except Exception:
        return False


def warmup_ollama() -> None:
    """Pre-load the local model so the first real call does not time out."""
    if not ollama_available():
        return
    try:
        _ollama_call("ping", model=ollama_model_for(FAST), timeout_ms=120_000)
    except Exception:  # noqa: BLE001
        pass


def _ollama_call(contents, config=None, timeout_ms: int | None = None,
                 model: str | None = None):
    """Run one generation through a local Ollama server.

    Returns a ``_Reply`` on success and ``None`` when the request cannot be
    served locally (no server, no text, images, or tool calling). Keeping this
    as a separate helper lets ``call()`` fall through to the Gemini ladder.
    """
    import json as _json
    import urllib.request
    import urllib.error

    # Tool calling and multimodal prompts stay on Gemini; Ollama is used for
    # the free text/JSON work that dominates long HOI4 sessions.
    if config is not None and getattr(config, "tools", None):
        return None

    import base64 as _b64

    items = contents if isinstance(contents, (list, tuple)) else [contents]
    text_parts: list[str] = []
    images: list[str] = []
    for item in items:
        if isinstance(item, str):
            text_parts.append(item)
            continue
        blob = getattr(item, "inline_data", None)
        if blob is not None:
            data = getattr(blob, "data", None)
            if isinstance(data, (bytes, bytearray)):
                images.append(_b64.b64encode(bytes(data)).decode("ascii"))
            continue
        txt = getattr(item, "text", None)
        if txt:
            text_parts.append(str(txt))
            continue
        if isinstance(item, dict):
            if "inline_data" in item:
                data = item["inline_data"].get("data") if isinstance(item["inline_data"], dict) else None
                if isinstance(data, (bytes, bytearray)):
                    images.append(_b64.b64encode(bytes(data)).decode("ascii"))
                elif isinstance(data, str):
                    images.append(data)
            elif "text" in item:
                text_parts.append(str(item["text"]))
    prompt = "\n".join(part for part in text_parts if part.strip())
    if not prompt.strip() and not images:
        return None

    system = ""
    if config is not None:
        system = (
            getattr(config, "system_instruction", None)
            or (config.get("system_instruction") if isinstance(config, dict) else "")
            or ""
        )

    payload = {
        "model": model or ollama_model(),
        "prompt": prompt,
        "stream": False,
        "keep_alive": ollama_keep_alive(),
    }
    if images:
        payload["images"] = images
    # Ask Ollama for strict JSON only when the prompt actually requests it.
    # Forcing JSON on every call would corrupt ordinary text answers.
    if "json" in prompt.lower():
        payload["format"] = "json"
    if system:
        payload["system"] = str(system)

    data = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ollama_base_url() + "/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    timeout = max(60.0, (timeout_ms or 60_000) / 1000.0)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Model may not be installed (404). Fall back to the shared text model
        # for non-vision requests; for vision, give up so Gemini can try.
        if exc.code == 404 and not images and (model or ollama_model()) != ollama_model():
            return _ollama_call(contents, config=config, timeout_ms=timeout_ms,
                               model=ollama_model())
        return None
    text = str(obj.get("response", "") or "").strip()
    if not text:
        return None
    return _Reply(text)


def call(
    contents,
    tier: str = FAST,
    config=None,
    timeout_ms: int | None = None,
    key: str = "",
):
    """Run one generation, walking the ladder until one answers.

    Returns the SDK's own response object, so callers that need more than the
    text — grounding metadata, candidates, usage — still get it. Returns None
    when every model on the ladder failed; the reason for each is printed, since
    a silent None during a session nobody can debug is how the original problem
    stayed hidden.
    """
    # Named tiers select a task profile. Anything else is an explicit model
    # name — screen_agent lets the user pick one in its settings — and it is
    # tried first, with the general reasoning ladder behind it.
    ladder = _LADDERS.get(tier)
    if ladder is None:
        ladder = (tier,) + tuple(m for m in _LADDERS[SMART] if m != tier)

    # Live API is for audio/text value generation; function calling is handled
    # by REST Flash so the caller receives structured function_call parts.
    if config is not None and getattr(config, "tools", None):
        ladder = tuple(model for model in ladder if model != LIVE)

    timeout_profile = tier if tier in _TIMEOUT_PROFILES else "default"
    timeout_ms = timeout_ms_for(timeout_profile, timeout_ms)

    # Prefer a local, free model for long-running work such as HOI4 planning
    # when one is available. This keeps the assistant playing without touching
    # the (quota-limited) Gemini ladder.
    global _ollama_failures, _ollama_cooldown_until, _gqt_failures, _groq_cooldown_until
    if ollama_available() and time.monotonic() >= _ollama_cooldown_until:
        local_model = ollama_model_for(tier)
        try:
            reply = _ollama_call(contents, config=config, timeout_ms=timeout_ms,
                                 model=local_model)
            if reply is not None and getattr(reply, "text", None):
                with _ollama_lock:
                    _ollama_failures = 0
                logger.info(f"задача={tier} выбрана модель ollama/{local_model}")
                return reply
            raise RuntimeError("empty reply from Ollama")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ollama: {type(exc).__name__}: {str(exc)[:140]}")
            with _ollama_lock:
                _ollama_failures += 1
                if _ollama_failures >= 3:
                    _ollama_cooldown_until = time.monotonic() + 300.0
                    _ollama_failures = 0

    # Собираем доступные ключи в порядке приоритета: явно переданный,
    # затем основной (ключ 1), затем резервный (ключ 2).
    keys = []
    if key:
        keys.append(("explicit", key))
    k1 = api_key(key_num=1)
    if k1:
        keys.append(("primary", k1))
    k2 = api_key(key_num=2)
    if k2:
        keys.append(("fallback", k2))

    if not keys:
        logger.warning("[Gemini] не настроен ключ API Gemini")
        # Gemini keys are exhausted — still try alternative providers below.

    # Порядок попыток: сначала самые сильные модели из лесенки, каждая
    # перебирается на всех доступных ключах (сначала основной, потом резервный).
    # Это гарантирует, что сильнейшие модели используются в первую очередь на
    # обоих ключах, и только при их полном исчерпании переходим к следующей модели.
    attempts = []
    for model in ladder:
        if _cooling(model):
            continue
        for label, k in keys:
            attempts.append((model, label, k))

    if not attempts:
        # Все модели отдыхают — всё равно попробуем, чтобы не молчать.
        for model in ladder:
            for label, k in keys:
                attempts.append((model, label, k))

    cl = None
    current_key = None
    both_keys_drained = False  # если оба ключа упали по квоте на одной модели — fallback для остальных не нужен
    for model, label, k in attempts:
        if both_keys_drained and label == "fallback":
            continue  # второй ключ скорее всего тоже пуст — не тратим время на лишний запрос
        try:
            if model == LIVE:
                reply = _live_call(contents, config, timeout_ms, k)
                if reply is not None:
                    logger.info(f"задача={tier} выбрана модель={model} [{label}]")
                    return reply
                raise RuntimeError("the Live turn came back empty")
            if cl is None or current_key != k:
                cl = client(
                    timeout_ms=timeout_ms,
                    key=k,
                    profile=timeout_profile,
                )
                current_key = k
            # Проверяем локальную оценку квоты перед реальным запросом,
            # чтобы не тратить время на холостые вызовы с таймаутом.
            if not _quota.can_proceed(k):
                logger.warning(
                    f"{model} [{label}]: локальная оценка квоты исчерпана — "
                    f"пропуск без запроса"
                )
                continue
            kwargs = {"model": model, "contents": contents}
            if config is not None:
                kwargs["config"] = config
            response = cl.models.generate_content(**kwargs)
            _quota.record(k, True)
            logger.info(f"задача={tier} выбрана модель={model} [{label}]")
            return response
        except Exception as e:
            msg = str(e)
            if is_quota_error(msg):
                rest = _quota_cooldown(msg)
                _cool(model, rest)
                _quota.record(k, False, is_quota_error=True)
                if label == "fallback":
                    both_keys_drained = True
                    logger.warning(
                        f"{model} [fallback]: квота исчерпана на обоих ключах — "
                        f"для остальных моделей пробуем только основной ключ."
                    )
                logger.warning(
                    f"{model} [{label}]: превышена квота — пропуск на "
                    f"{_quota_rest_label(rest)}"
                )
                continue
            elif is_gone_error(msg):
                _cool(model, _GONE_SECONDS)
                logger.warning(
                    f"{model} [{label}]: недоступен для этого ключа — откладываем в сторону"
                )
                continue
            elif is_unavailable_error(msg):
                _cool(model, _UNAVAILABLE_SECONDS)
                logger.warning(
                    f"{model} [{label}]: не отвечает — отдыхает "
                    f"{_UNAVAILABLE_SECONDS // 60} минут"
                )
                continue
            else:
                logger.warning(f"{model} [{label}]: {type(e).__name__}: {msg[:140]}")
                continue

    # Gemini ladder exhausted — fall back to alternative providers.
    if _groq_available() and time.monotonic() >= _groq_cooldown_until:
        reply = _groq_call(contents, config=config, timeout_ms=timeout_ms)
        if reply is not None and getattr(reply, "text", None):
            with _gqt_lock:
                _gqt_failures = 0
            logger.info(f"задача={tier} выбрана модель=groq")
            return reply

    if _github_available():
        reply = _github_call(contents, config=config, timeout_ms=timeout_ms)
        if reply is not None and getattr(reply, "text", None):
            logger.info(f"задача={tier} выбрана модель=github")
            return reply

    return None


def text(
    contents,
    tier: str = FAST,
    config=None,
    timeout_ms: int | None = None,
    key: str = "",
    default: str = "",
) -> str:
    """`call`, reduced to the reply text. `default` when nothing answered."""
    resp = call(contents, tier=tier, config=config, timeout_ms=timeout_ms, key=key)
    if resp is None:
        return default
    return (getattr(resp, "text", None) or "").strip() or default


def as_json(
    contents,
    tier: str = FAST,
    config=None,
    timeout_ms: int | None = None,
    key: str = "",
    default=None,
):
    """`text`, parsed as JSON, tolerating the fences and prose a model wraps it
    in. `default` when nothing answered or the answer would not parse."""
    raw = text(contents, tier=tier, config=config, timeout_ms=timeout_ms, key=key)
    if not raw:
        return default
    if "{" in raw and "}" in raw:
        raw = raw[raw.find("{") : raw.rfind("}") + 1]
    elif "[" in raw and "]" in raw:
        raw = raw[raw.find("[") : raw.rfind("]") + 1]
    try:
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"ответ не JSON: {e}")
        return default
