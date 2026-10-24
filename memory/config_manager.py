import json
import sys
from pathlib import Path


try:
    from core.paths import APP_DIR, CONFIG_DIR, ensure_user_dirs

    BASE_DIR = APP_DIR
    ensure_user_dirs()
    CONFIG_FILE = CONFIG_DIR / "api_keys.json"
    # Fallback: если в user-data конфига ещё нет, а в папке приложения есть —
    # читаем оттуда (ensure_user_dirs уже попробовал скопировать, но на случай
    # гонки/ошибки копирования не требуем повторный ввод).
    try:
        _legacy = BASE_DIR / "config" / "api_keys.json"
        if (not CONFIG_FILE.exists()) and _legacy.exists():
            import shutil as _shutil

            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                _shutil.copy2(_legacy, CONFIG_FILE)
            except Exception:
                CONFIG_FILE = _legacy
    except Exception:
        pass
except Exception:

    def get_base_dir() -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).parent
        return Path(__file__).resolve().parent.parent

    BASE_DIR = get_base_dir()
    try:
        from core.paths import CONFIG_DIR, ensure_user_dirs  # type: ignore

        ensure_user_dirs()
        CONFIG_FILE = CONFIG_DIR / "api_keys.json"
    except Exception:
        CONFIG_DIR = BASE_DIR / "config"
        CONFIG_FILE = CONFIG_DIR / "api_keys.json"


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def config_exists() -> bool:
    return CONFIG_FILE.exists()


def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data["gemini_api_key"] = gemini_api_key.strip()

    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_api_keys() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"❌ Failed to load api_keys.json: {e}")
            return {}
    # Fallback на legacy-файл в папке приложения (до миграции)
    try:
        _legacy = BASE_DIR / "config" / "api_keys.json"
        if _legacy.exists():
            try:
                return json.loads(_legacy.read_text(encoding="utf-8"))
            except Exception:
                return {}
    except Exception:
        pass
    return {}


def get_gemini_key() -> str | None:
    return load_api_keys().get("gemini_api_key")


def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'JARVIS' if not set."""
    return load_api_keys().get("assistant_name", "JARVIS") or "JARVIS"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["assistant_name"] = assistant_name.strip() or "JARVIS"
    data["user_name"] = user_name.strip()
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE = "Charon"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Jarvis')."""
    return load_api_keys().get("wake_word_enabled", False)


def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["wake_word_enabled"] = bool(enabled)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


def get_offline_asr_enabled() -> bool:
    """Use Windows SAPI to recognize microphone speech before sending text to Gemini."""
    return bool(load_api_keys().get("offline_asr_enabled", False))


def save_offline_asr_enabled(enabled: bool) -> None:
    _save_flag("offline_asr_enabled", enabled)


def get_voice_affect_enabled() -> bool:
    """Whether JARVIS locally estimates vocal delivery from short mic clips.

    Read the former combined setting as a migration fallback. The webcam is
    never started by this preference.
    """
    data = load_api_keys()
    return bool(data.get("voice_affect_enabled",
                         data.get("emotion_recognition_enabled", True)))


def save_voice_affect_enabled(enabled: bool) -> None:
    _save_flag("voice_affect_enabled", enabled)


# Backward-compatible names for older plugins; these now control voice only.
def get_emotion_recognition_enabled() -> bool:
    return get_voice_affect_enabled()


def save_emotion_recognition_enabled(enabled: bool) -> None:
    save_voice_affect_enabled(enabled)


HUD_STYLES = ("face", "core")

# Nova's selectable answer-length modes, adapted to JARVIS's live session.
RESPONSE_STYLES = ("concise", "normal", "detailed")


def get_response_style() -> str:
    value = str(load_api_keys().get("response_style", "normal")).strip().lower()
    return value if value in RESPONSE_STYLES else "normal"


def save_response_style(style: str) -> None:
    value = str(style or "").strip().lower()
    _save_flag("response_style", value if value in RESPONSE_STYLES else "normal")


def get_hud_style() -> str:
    """Which centrepiece the HUD draws: the animated head, or the reactor core.

    Taste, not capability — both render in the same software painter and cost
    about the same. Defaults to the head because that is what MARK LIV shipped
    with; anyone who preferred the older look can switch back in ⚙ and the
    choice survives a restart.
    """
    v = str(load_api_keys().get("hud_style", "face")).strip().lower()
    return v if v in HUD_STYLES else "face"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip().lower()
    _save_flag("hud_style", s if s in HUD_STYLES else "face")


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.


def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms": _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity": str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


def save_turn_tuning(values: dict) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cur = data.get("turn_tuning")
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    data["turn_tuning"] = cur
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[key] = bool(value) if isinstance(value, bool) else value
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["morning_brief_enabled"] = enabled
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_telegram_watch_enabled() -> bool:
    """Whether JARVIS keeps watching the owner's Telegram and speaks new messages."""
    return load_api_keys().get("telegram_watch_enabled", False)


def save_telegram_watch_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["telegram_watch_enabled"] = bool(enabled)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_telegram_read_channels() -> list:
    """Broadcast channels JARVIS reads (Telegram IDs or @usernames, as strings).

    Chosen by the owner in settings. Empty list = no broadcast channels are read;
    private chats and selected groups follow their separate read policies.
    """
    raw = load_api_keys().get("telegram_read_channels", [])
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def save_telegram_read_channels(channels) -> None:
    _patch_config(telegram_read_channels=[str(x).strip() for x in (channels or []) if str(x).strip()])


def get_telegram_read_groups() -> list:
    """Human Telegram groups JARVIS reads (IDs/usernames stored as strings)."""
    raw = load_api_keys().get("telegram_read_groups", [])
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def save_telegram_read_groups(groups) -> None:
    _patch_config(telegram_read_groups=[str(x).strip() for x in (groups or []) if str(x).strip()])


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.


def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(fields)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_gemini_request_timeout_ms() -> int:
    """Shared Gemini request deadline, bounded to 10 seconds–5 minutes."""
    try:
        settings = load_api_keys()
        if not isinstance(settings, dict):
            return 10_000
        value = settings.get("gemini_request_timeout_ms", 10_000)
        if isinstance(value, bool):
            return 10_000
        return max(10_000, min(300_000, int(value)))
    except Exception:
        return 10_000


def set_gemini_request_timeout_ms(timeout_ms: int) -> bool:
    """Save the shared Gemini request deadline without touching other settings."""
    if isinstance(timeout_ms, bool):
        return False
    try:
        value = int(timeout_ms)
    except (TypeError, ValueError, OverflowError):
        return False
    if not 10_000 <= value <= 300_000:
        return False
    ensure_config_dir()
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False
        except (OSError, ValueError):
            # Never replace a damaged file that may still contain API keys.
            return False
    else:
        data = load_api_keys() or {}
        if not isinstance(data, dict):
            return False
    data["gemini_request_timeout_ms"] = value
    try:
        CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
        return True
    except OSError:
        return False


def set_autonomy_auto_adopt(enabled: bool = True,
                            max_per_day: int = 144,
                            scan_interval_seconds: int = 600) -> bool:
    """Set GitHub auto-adoption policy without replacing other settings."""
    ensure_config_dir()
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False
        except (OSError, ValueError):
            # Do not overwrite a damaged file that may contain API credentials.
            return False
    else:
        data = load_api_keys() or {}
        if not isinstance(data, dict):
            return False
    data["autonomy_auto_adopt"] = bool(enabled)
    data["autonomy_max_adopts_per_day"] = max(1, min(int(max_per_day), 144))
    data["autonomy_scout_every"] = max(600, min(int(scan_interval_seconds), 86400))
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return True


def set_autonomy_idle_mode(enabled: bool = True,
                           idle_after_seconds: int = 300) -> bool:
    """Configure activity-gated background improvement without replacing keys."""
    try:
        value = int(idle_after_seconds)
    except (TypeError, ValueError, OverflowError):
        return False
    if not 60 <= value <= 3600:
        return False
    ensure_config_dir()
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False
        except (OSError, ValueError):
            # Never replace a damaged file that may contain API keys.
            return False
    else:
        data = load_api_keys() or {}
        if not isinstance(data, dict):
            return False
    data["autonomy_idle_only"] = bool(enabled)
    data["autonomy_idle_after_seconds"] = value
    # Background discovery is fine; adding third-party executable code is not.
    data["autonomy_auto_adopt"] = False
    try:
        CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
        return True
    except OSError:
        return False


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


# ── Android device unlock ────────────────────────────────────────────────────
# Optional PIN/password JARVIS types on the device's lock screen before it
# starts a request (e.g. "поставь музыку на планшете"). The user enabled ADB
# on their own device; this only automates the code they would enter by hand.
# It is stored in plain text in config/api_keys.json — the same file as the
# Gemini key — so it is only as private as that file. Leave it empty to keep
# the old behaviour (JARVIS asks the user to unlock manually).
#
# Passwords are stored per device in ``tablet_unlock_passwords``, keyed by ADB
# serial; the empty key is the default. The legacy top-level
# ``tablet_unlock_password`` stays in sync with the default for older configs.
def get_tablet_unlock_password(device_id: str = "") -> str:
    """The stored unlock PIN/password for a device (or the default), or ''."""
    cfg = load_api_keys()
    store = cfg.get("tablet_unlock_passwords")
    legacy = str(cfg.get("tablet_unlock_password") or "")
    if isinstance(store, dict):
        key = (device_id or "").strip()
        if key and store.get(key):
            return str(store[key] or "")
        if store.get("") is not None:
            return str(store[""] or "")
    return legacy


def save_tablet_unlock_password(password: str, device_id: str = "") -> None:
    """Persist the device unlock PIN/password (empty string clears it).

    ``device_id`` is the ADB serial; the empty string selects the default used
    when a device has no password of its own.
    """
    key = (device_id or "").strip()
    data = load_api_keys()
    store = data.get("tablet_unlock_passwords")
    if not isinstance(store, dict):
        store = {}
    if password:
        store[key] = str(password)
    else:
        store.pop(key, None)
    if key:
        _patch_config(tablet_unlock_passwords=store)
        return
    if password:
        _patch_config(
            tablet_unlock_password=str(password),
            tablet_unlock_passwords=store,
        )
    else:
        data.pop("tablet_unlock_password", None)
        data["tablet_unlock_passwords"] = store
        CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def has_tablet_unlock_password(device_id: str = "") -> bool:
    return bool(get_tablet_unlock_password(device_id))


# ── Android voice routing ─────────────────────────────────────────────────────
# The ADB device JARVIS streams its spoken reply to (the tablet/phone speaker)
# when the user asks for it. Remembered so "включи звук на планшете" keeps
# working by name once one device has been chosen.
def get_android_voice_device() -> str:
    """Saved ADB serial JARVIS routes its voice to, or '' if none."""
    return str(load_api_keys().get("android_voice_device") or "")


def save_android_voice_device(device_id: str) -> None:
    _patch_config(android_voice_device=(device_id or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    cur.update(values)
    pc[namespace] = cur
    data["plugin_config"] = pc
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
