"""Synthesise arbitrary text to 24 kHz mono PCM with the Live model.

JARVIS's own voice is the audio the Live session streams back while it talks.
This module is the other half of that: given a plain string, it asks the same
Live model — same voice, same language handling — for nothing but a reading of
that string, and returns raw PCM in the format the Android voice router plays
(24000 Hz, 16-bit, mono).

It is deliberately a *text-to-speech* call and not a conversational one: the
system instruction tells the model it is an engine, not an assistant, so the
returned audio is the user's text and not a reply to it. No tools are offered,
so the turn cannot do anything but speak.
"""

from __future__ import annotations

import asyncio

# The Live API streams 24 kHz / 16-bit / mono PCM back — the same rate the
# voice router opens its AudioTrack with.
SAMPLE_RATE = 24000

_SYSTEM = (
    "You are a text-to-speech engine, not an assistant. The user's message is "
    "the exact text to read aloud, in its original language. Reproduce it "
    "verbatim: do not translate, summarise, expand, censor, soften, rephrase or "
    "answer it, and add no words of your own. Speak only the given text."
)


def _settings() -> tuple[str, str, str]:
    """(api_key, model, voice) from the same config the assistant uses."""
    from memory.config_manager import get_voice, load_api_keys

    data = load_api_keys() or {}
    key = str(data.get("gemini_api_key") or "")
    model = str(data.get("gemini_live_model") or "") or "models/gemini-3.8-live"
    try:
        voice = get_voice()
    except Exception:
        voice = "Charon"
    return key, model, voice


async def _chunks(text: str, key: str, model: str, voice: str):
    """Yield 24 kHz mono 16-bit PCM as the Live model produces it.

    Chunks are handed over the moment they arrive instead of being collected,
    so a caller that plays them can start after the first one instead of paying
    the whole line's generation *and* its playback one after the other.
    """
    from google import genai
    from google.genai import types as gtypes
    from core import gemini

    client = genai.Client(api_key=key, http_options={"api_version": "v1alpha"})
    cfg = gtypes.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=_SYSTEM,
        speech_config=gtypes.SpeechConfig(
            voice_config=gtypes.VoiceConfig(
                prebuilt_voice_config=gtypes.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
    )
    connect_timeout = gemini.timeout_ms_for("live_connect")
    turn_timeout = gemini.timeout_ms_for("tts")
    # Live is a WebSocket stream, so the SDK's HTTP request timeout does not
    # bound it. Put a deadline around both connection setup and audio output.
    async with asyncio.timeout((connect_timeout + turn_timeout) / 1000):
        async with client.aio.live.connect(model=model, config=cfg) as session:
            await session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]}, turn_complete=True
            )
            async for response in session.receive():
                data = getattr(response, "data", None)
                if data:
                    yield data
                sc = getattr(response, "server_content", None)
                if sc is not None and getattr(sc, "turn_complete", False):
                    break


async def speak_stream(text: str):
    """Async generator of PCM chunks for ``text`` in JARVIS's voice."""
    text = (text or "").strip()
    if not text:
        return
    key, model, voice = _settings()
    if not key:
        raise RuntimeError("Не настроен ключ Gemini API — синтез речи невозможен.")
    async for chunk in _chunks(text, key, model, voice):
        yield chunk


def synth_pcm(text: str, *, timeout: float = 90.0) -> bytes:
    """Turn ``text`` into 24 kHz mono 16-bit PCM. Empty string -> empty bytes."""
    text = (text or "").strip()
    if not text:
        return b""
    key, model, voice = _settings()
    if not key:
        raise RuntimeError("Не настроен ключ Gemini API — синтез речи невозможен.")

    async def _collect() -> bytes:
        out = bytearray()
        async for chunk in _chunks(text, key, model, voice):
            out.extend(chunk)
        return bytes(out)

    return asyncio.run(asyncio.wait_for(_collect(), timeout))
