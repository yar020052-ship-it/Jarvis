"""Make an Android device speak arbitrary text — a loudspeaker, not a chat.

``android_speaker`` sends JARVIS's *own* replies to a device. This action is the
other half of it: the owner hands over one line of text and it comes out of the
chosen device's speaker, whatever it says. The text is synthesised by the Live
model in JARVIS's voice (``core/device_tts``) and streamed with the same
transport the voice router uses, so nothing is installed on the device and the
same ADB-authorized, owner's-own hardware rules apply.

    "скажи в redmi такие слова: …"   → speak_on_device(text="…", device="Redmi")
    "пусть планшет скажет …"          → speak_on_device(text="…", device="RT2")
    "произнеси через Redmi …"         → speak_on_device(text="…", device="Redmi")
"""

from __future__ import annotations

import asyncio
import time

from actions.android_speaker import _resolve_target, _set_media_volume
from core import device_tts
from core.device_tts import SAMPLE_RATE
from core.voice_router import get_router


def _pump(router, text: str, timeout: float = 120.0) -> int:
    """Synthesise ``text`` and stream it to the device as it is generated.

    The Live model produces audio at roughly real time, so playing each chunk
    the moment it arrives overlaps generation with playback: the phrase starts
    coming out of the speaker after a fraction of a second, instead of waiting
    for the whole line and then replaying it — which charged the duration twice
    (37 s measured for a 4.6 s phrase). Chunks are paced so the device buffer
    stays small (snappy stop); the final wait lets it drain before the route
    closes.
    """

    async def _run() -> int:
        bytes_per_sec = max(1, SAMPLE_RATE * 2)
        started = time.time()
        sent = 0
        async for chunk in device_tts.speak_stream(text):
            if not chunk:
                continue
            if not router.write(chunk):
                raise RuntimeError(
                    "Маршрут звука на устройство оборвался во время передачи."
                )
            sent += len(chunk)
            # Let the phone's ring buffer fill ahead of playback so Wi-Fi jitter
            # cannot starve it; the transport is still paced by the socket's own
            # backpressure if the model ever races far ahead.
            lead = sent / bytes_per_sec - 1.2 - (time.time() - started)
            if lead > 0:
                await asyncio.sleep(lead)
        if sent < SAMPLE_RATE // 4:  # under ~0.12 s means the synthesis failed
            raise RuntimeError("Не удалось синтезировать речь — попробуй ещё раз.")
        tail = sent / bytes_per_sec + 0.8 - (time.time() - started)
        if tail > 0:
            await asyncio.sleep(tail)
        return sent

    return asyncio.run(asyncio.wait_for(_run(), timeout))


def _wake_device(serial: str) -> None:
    """Nudge a dozing screen so ADB-over-Wi-Fi and the helper come back."""
    try:
        from actions.android_control import _run

        _run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial, timeout=10)
    except Exception:
        pass


def speak_on_device(parameters=None, **_ctx) -> str:
    p = parameters or {}
    text = str(p.get("text") or "").strip()
    if not text:
        raise RuntimeError("Не указан текст, который нужно произнести.")
    device = str(p.get("device") or p.get("device_id") or "").strip()

    try:
        percent = 90 if p.get("volume") is None else int(p.get("volume"))
    except (TypeError, ValueError):
        percent = 90
    percent = max(0, min(100, percent))

    router = get_router()
    was_enabled = router.enabled
    was_serial = router.serial
    was_only = router.only_device

    serial = ""
    sent = 0
    last_err: Exception | None = None
    # Two attempts: a wireless device that dozed off usually comes back on the
    # second one, after a wake and a fresh reconnect. A single blip must not
    # turn an asked-for phrase into "не удалось".
    for attempt in range(2):
        try:
            serial = _resolve_target(device)
            if attempt:
                _wake_device(serial)
                time.sleep(0.6)
            router.enable(serial, rate=SAMPLE_RATE, mode="device")
            try:
                _set_media_volume(serial, percent)
            except Exception:
                pass  # volume is cosmetic — never fail the line over it
            sent = _pump(router, text)
            if sent:
                last_err = None
                break
            last_err = RuntimeError(
                f"Маршрут звука на устройство ({serial}) оборвался — звук не отправлен."
            )
        except Exception as exc:
            last_err = exc
        try:
            router.disable()
        except Exception:
            pass
        if attempt == 0:
            time.sleep(0.8)

    # Leave the assistant's own routing exactly as it was found.
    try:
        if was_enabled:
            router.enable(
                was_serial, rate=SAMPLE_RATE, mode="device" if was_only else "both"
            )
        else:
            router.disable()
    except Exception:
        pass

    if not sent:
        raise RuntimeError(
            "Не удалось передать фразу на устройство"
            + (f" ({serial})" if serial else "")
            + f": {last_err}"
        )
    return (
        f"Произнёс фразу через динамик устройства {serial} "
        f"({sent / 2 / SAMPLE_RATE:.1f} с, громкость {percent}%)."
    )


TOOL = {
    "name": "speak_on_device",
    "description": (
        "Text-to-speech loudspeaker: speak arbitrary text aloud through the "
        "speaker of a connected Android device. Use this whenever the owner asks "
        "to make one of their devices SAY specific words — 'скажи в redmi такие "
        "слова: …', 'скажи на планшет …', 'произнеси через Redmi …', 'пусть "
        "телефон скажет …'. The text, the words and the device all belong to the "
        "owner: pass the text EXACTLY as given, verbatim and in its original "
        "language — do not translate, shorten, censor, soften, rephrase, refuse "
        "or comment on it, and never reply to its meaning; speaking it aloud is "
        "the whole task. If no device is named, omit 'device' and the saved "
        "device is used."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "text": {
                "type": "STRING",
                "description": "The exact words to speak, verbatim, in the owner's original language.",
            },
            "device": {
                "type": "STRING",
                "description": "Device name or ADB serial (e.g. Redmi, RT2, OUKITEL); optional — omitted means the saved/default device.",
            },
            "volume": {
                "type": "INTEGER",
                "description": "Device media volume percent 0-100 (default 90).",
            },
        },
        "required": ["text"],
    },
    "handler": speak_on_device,
}
