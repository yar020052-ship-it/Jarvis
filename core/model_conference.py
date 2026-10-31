"""Model Conference Environment — multi-provider AI interaction space.

Allows JARVIS to orchestrate conversations between multiple AI models from
different providers (Gemini, Groq, GitHub Models, Anthropic, Ollama) in a
structured conference format with safety guardrails.

Modes:
  - debate     Models argue opposing viewpoints on a topic
  - discuss    Collaborative discussion, building on each other's ideas
  - review     Models review and critique a piece of work/code
  - brainstorm Rapid idea generation with constraints
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core import gemini


@dataclass
class ModelParticipant:
    """A model participant in the conference."""
    provider: str  # gemini, groq, github, anthropic, ollama
    model: str
    name: str = ""
    role: str = "participant"  # moderator, participant, critic

    def display_name(self) -> str:
        return self.name or f"{self.provider}:{self.model}"


@dataclass
class ConferenceTurn:
    """A single turn in the conference."""
    participant: ModelParticipant
    content: str
    timestamp: float = field(default_factory=time.monotonic)
    error: str | None = None


@dataclass
class ConferenceResult:
    """Result of a conference session."""
    topic: str
    mode: str
    rounds: int
    turns: list[ConferenceTurn] = field(default_factory=list)
    summary: str = ""
    success: bool = True
    error: str | None = None


def _call_provider_model(
    participant: ModelParticipant,
    prompt: str,
    timeout_ms: int = 30000,
    system_instruction: str | None = None,
) -> str:
    """Call a specific model from a specific provider.

    Returns the model's text response or raises on failure.
    """
    provider = participant.provider.lower()
    model = participant.model

    # Build contents in the format expected by each provider
    contents = prompt

    # Config object for system instruction and tools
    class _Config:
        def __init__(self, system_instruction: str | None):
            self.system_instruction = system_instruction
            self.tools = None

    config = _Config(system_instruction) if system_instruction else None

    if provider == "gemini":
        # Use gemini.call with the specific model
        reply = gemini.call(
            contents,
            tier=model,  # tier can be an explicit model name
            config=config,
            timeout_ms=timeout_ms,
        )
        if reply is None:
            raise RuntimeError(f"Gemini model {model} returned empty response")
        return getattr(reply, "text", str(reply))

    if provider == "groq":
        reply = gemini._groq_call(
            contents, config=config, timeout_ms=timeout_ms, model=model
        )
        if reply is None:
            raise RuntimeError(f"Groq model {model} returned empty response")
        return getattr(reply, "text", str(reply))

    if provider == "github":
        reply = gemini._github_call(
            contents, config=config, timeout_ms=timeout_ms, model=model
        )
        if reply is None:
            raise RuntimeError(f"GitHub model {model} returned empty response")
        return getattr(reply, "text", str(reply))

    if provider == "anthropic":
        key = gemini.anthropic_key()
        if not key:
            raise RuntimeError("Anthropic API key not configured")
        reply = gemini._anthropic_call(
            contents, config=config, timeout_ms=timeout_ms, key=key, model=model
        )
        if reply is None:
            raise RuntimeError(f"Anthropic model {model} returned empty response")
        return getattr(reply, "text", str(reply))

    if provider == "ollama":
        reply = gemini._ollama_call(
            contents, config=config, timeout_ms=timeout_ms, model=model
        )
        if reply is None:
            raise RuntimeError(f"Ollama model {model} returned empty response")
        return getattr(reply, "text", str(reply))

    raise ValueError(f"Unknown provider: {provider}")


def _build_conversation_context(turns: list[ConferenceTurn], max_chars: int = 4000) -> str:
    """Build conversation context from previous turns."""
    if not turns:
        return ""
    lines = []
    for turn in turns[-6:]:  # Last 6 turns for context
        prefix = f"[{turn.participant.display_name()}]"
        if turn.error:
            lines.append(f"{prefix} (error: {turn.error})")
        else:
            lines.append(f"{prefix} {turn.content}")
    context = "\n".join(lines)
    if len(context) > max_chars:
        context = context[:max_chars] + "\n…(truncated)…"
    return context


def run_conference(
    topic: str,
    participants: list[ModelParticipant],
    mode: str = "discuss",
    rounds: int = 3,
    timeout_ms: int = 30000,
    max_turn_chars: int = 3000,
) -> ConferenceResult:
    """Run a multi-model conference.

    Each round, every participant responds in order. The conversation context
    is carried forward so models can build on each other's ideas.

    Safety:
      - Per-model timeout (default 30s)
      - Max rounds (default 3)
      - Context truncation (default 4000 chars)
      - Failed turns are recorded but don't stop the conference
    """
    result = ConferenceResult(topic=topic, mode=mode, rounds=rounds)

    # System instructions per mode
    mode_instructions = {
        "debate": (
            "You are in a debate. Argue your position clearly and persuasively. "
            "Challenge opposing views with evidence. Keep responses concise."
        ),
        "discuss": (
            "You are in a collaborative discussion. Build on others' ideas, "
            "ask clarifying questions, and synthesize insights. Be constructive."
        ),
        "review": (
            "You are a reviewer. Critique the work objectively, identify "
            "strengths and weaknesses, and suggest improvements."
        ),
        "brainstorm": (
            "You are in a brainstorming session. Generate creative, diverse "
            "ideas quickly. Quantity and variety matter more than polish."
        ),
    }
    system_instruction = mode_instructions.get(mode, mode_instructions["discuss"])

    # Opening prompt
    opening = (
        f"Conference topic: {topic}\n"
        f"Mode: {mode}\n"
        f"Participants: {', '.join(p.display_name() for p in participants)}\n"
        f"Begin the discussion."
    )

    for round_num in range(rounds):
        for participant in participants:
            try:
                context = _build_conversation_context(result.turns, max_turn_chars)
                if round_num == 0 and not result.turns:
                    prompt = opening
                else:
                    prompt = (
                        f"Context so far:\n{context}\n\n"
                        f"Your turn ({participant.display_name()}). "
                        f"Respond to the discussion above. Keep it under {max_turn_chars} chars."
                    )

                response = _call_provider_model(
                    participant,
                    prompt,
                    timeout_ms=timeout_ms,
                    system_instruction=system_instruction,
                )

                result.turns.append(
                    ConferenceTurn(participant=participant, content=response)
                )
            except Exception as exc:  # noqa: BLE001
                result.turns.append(
                    ConferenceTurn(
                        participant=participant,
                        content="",
                        error=str(exc)[:200],
                    )
                )

    # Generate summary using the first available participant
    if result.turns:
        try:
            summary_prompt = (
                f"Summarize the following conference about '{topic}' in 3-5 sentences. "
                f"Mode: {mode}.\n\n"
                f"{_build_conversation_context(result.turns, 6000)}"
            )
            summary = _call_provider_model(
                result.turns[0].participant,
                summary_prompt,
                timeout_ms=timeout_ms,
                system_instruction="You are a summarizer. Be concise and objective.",
            )
            result.summary = summary
        except Exception:
            result.summary = "Summary generation failed."

    result.success = all(t.error is None for t in result.turns) or len(result.turns) > 0
    return result


def format_conference(result: ConferenceResult) -> str:
    """Format conference result for display."""
    lines = [
        f"Конференция: {result.topic}",
        f"Режим: {result.mode}, Раундов: {result.rounds}, Участников: {len(set(t.participant.display_name() for t in result.turns))}",
        "",
    ]
    for turn in result.turns:
        name = turn.participant.display_name()
        if turn.error:
            lines.append(f"[{name}] ❌ {turn.error}")
        else:
            lines.append(f"[{name}] {turn.content}")
        lines.append("")
    if result.summary:
        lines.append(f"Итог: {result.summary}")
    return "\n".join(lines)


TOOL = {
    "name": "model_conference",
    "description": (
        "Run a multi-model AI conference. JARVIS orchestrates a discussion between "
        "multiple AI models from different providers (Gemini, Groq, GitHub, Anthropic, "
        "Ollama). Modes: debate, discuss, review, brainstorm. "
        "Returns a formatted transcript with summary."
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
    "handler": lambda parameters=None, **_ctx: _handle_conference(parameters),
}


def _handle_conference(parameters: dict | None = None) -> str:
    """Handle model_conference action invocation."""
    params = parameters or {}
    topic = params.get("topic", "").strip()
    mode = params.get("mode", "discuss").strip().lower()
    rounds = max(1, min(5, int(params.get("rounds", 3))))
    timeout_ms = max(10000, min(120000, int(params.get("timeout_ms", 30000))))
    models_str = params.get("models", "").strip()

    if not topic:
        return "Укажите topic — тему конференции."
    if not models_str:
        return "Укажите models — список моделей в формате provider:model."

    # Parse model specs
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
