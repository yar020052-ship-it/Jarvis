"""Model Conference action — exposes core.model_conference to JARVIS.

Allows JARVIS to orchestrate conversations between multiple AI models from
different providers. Use via the model_conference tool.
"""

from __future__ import annotations

from typing import Any

from core.model_conference import run_conference, format_conference, ModelParticipant


def model_conference(parameters: dict | None = None, **_ctx) -> str:
    """Run a multi-model AI conference.

    Parameters:
        topic: Conference topic
        mode: debate | discuss | review | brainstorm
        rounds: Number of rounds (1-5)
        models: Comma-separated provider:model list
        timeout_ms: Per-model timeout in ms
    """
    params = parameters or {}
    topic = str(params.get("topic", "")).strip()
    mode = str(params.get("mode", "discuss")).strip().lower()
    rounds = max(1, min(5, int(params.get("rounds", 3))))
    timeout_ms = max(10000, min(120000, int(params.get("timeout_ms", 30000))))
    models_str = str(params.get("models", "")).strip()

    if not topic:
        return "Укажите topic — тему конференции."
    if not models_str:
        return "Укажите models — список моделей в формате provider:model."

    participants = []
    for spec in models_str.split(","):
        spec = spec.strip()
        if ":" not in spec:
            continue
        provider, model = spec.split(":", 1)
        participants.append(
            ModelParticipant(
                provider=provider.strip(),
                model=model.strip(),
                name=f"{provider.strip()}:{model.strip()}",
            )
        )

    if not participants:
        return "Не удалось распознать модели. Используйте формат provider:model."

    result = run_conference(
        topic=topic,
        participants=participants,
        mode=mode,
        rounds=rounds,
        timeout_ms=timeout_ms,
    )
    return format_conference(result)


TOOL = {
    "name": "model_conference",
    "description": (
        "Run a multi-model AI conference. JARVIS orchestrates a discussion between "
        "multiple AI models from different providers (Gemini, Groq, GitHub, Anthropic, "
        "Ollama). Modes: debate, discuss, review, brainstorm. Returns a formatted "
        "transcript with summary."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "topic": {"type": "STRING", "description": "Conference topic or question"},
            "mode": {
                "type": "STRING",
                "description": "debate, discuss, review, or brainstorm (default: discuss)",
            },
            "rounds": {
                "type": "INTEGER",
                "description": "Number of rounds (default: 3, max: 5)",
            },
            "models": {
                "type": "STRING",
                "description": "Comma-separated model specs: provider:model (e.g., 'groq:llama-3.3-70b,github:gpt-4.5-preview,gemini:gemini-2.5-flash')",
            },
            "timeout_ms": {
                "type": "INTEGER",
                "description": "Timeout per model call in ms (default: 30000)",
            },
        },
        "required": ["topic", "models"],
    },
    "handler": model_conference,
}
