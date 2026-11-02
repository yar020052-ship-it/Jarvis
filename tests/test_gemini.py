"""Tests for Gemini one-shot configuration and key cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core import gemini


@pytest.fixture(autouse=True)
def reset_key_cache(monkeypatch):
    monkeypatch.setattr(gemini, "_cached_key", None, raising=False)
    monkeypatch.setattr(gemini, "_cached_key_2", None, raising=False)
    monkeypatch.setattr(gemini, "_cached_key_signature", None, raising=False)
    # Reset fallback provider cooldown state between tests.
    monkeypatch.setattr(gemini, "_groq_cooldown_until", 0.0, raising=False)
    monkeypatch.setattr(gemini, "_gqt_failures", 0, raising=False)


def test_api_key_prefers_os_secret(monkeypatch):
    config_path = Path("api_keys.json")
    monkeypatch.setattr("core.paths.get_config_path", lambda _name: config_path)
    monkeypatch.setattr("core.gemini.load_config", lambda _name: {"gemini_api_key": "json-key"})
    monkeypatch.setattr("core.secrets.get_secret", lambda key: "secret-key")

    assert gemini.api_key() == "secret-key"


def test_api_key_falls_back_to_json(monkeypatch):
    config_path = Path("api_keys.json")
    monkeypatch.setattr("core.paths.get_config_path", lambda _name: config_path)
    monkeypatch.setattr("core.gemini.load_config", lambda _name: {"gemini_api_key": "json-key"})
    monkeypatch.setattr("core.secrets.get_secret", lambda key: None)

    assert gemini.api_key() == "json-key"


def test_api_key_missing_config_is_empty(monkeypatch):
    config_path = Path("api_keys.json")
    monkeypatch.setattr("core.paths.get_config_path", lambda _name: config_path)
    monkeypatch.setattr("core.gemini.load_config", lambda _name: {})
    monkeypatch.setattr("core.secrets.get_secret", lambda key: None)

    assert gemini.api_key() == ""


def test_api_key_cache_uses_refresh(monkeypatch):
    config_path = Path("api_keys.json")
    monkeypatch.setattr("core.paths.get_config_path", lambda _name: config_path)
    monkeypatch.setattr("core.gemini.load_config", lambda _name: {"gemini_api_key": "json-key"})

    secrets = {"value": "first"}
    monkeypatch.setattr("core.secrets.get_secret", lambda key: secrets["value"])

    assert gemini.api_key() == "first"

    # Cached value is retained while the config signature is unchanged.
    secrets["value"] = "second"
    assert gemini.api_key() == "first"

    # refresh=True bypasses the cache.
    assert gemini.api_key(refresh=True) == "second"


def _install_mock_module(monkeypatch, name, client_factory):
    """Install a fake module so ``from <name> import ...`` works inside gemini."""
    import sys
    import types

    mod = types.ModuleType(name)
    mod.Groq = client_factory
    mod.OpenAI = client_factory
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


def test_groq_key_from_config(monkeypatch):
    monkeypatch.setattr(
        "memory.config_manager.load_api_keys",
        lambda: {"groq_api_key": "gsk_test_key"},
    )
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert gemini.groq_key() == "gsk_test_key"


def test_groq_key_from_env(monkeypatch):
    monkeypatch.setattr("memory.config_manager.load_api_keys", lambda: {})
    monkeypatch.setenv("GROQ_API_KEY", "gsk_env_key")
    assert gemini.groq_key() == "gsk_env_key"


def test_groq_key_empty_when_not_configured(monkeypatch):
    monkeypatch.setattr("memory.config_manager.load_api_keys", lambda: {})
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert gemini.groq_key() == ""


def test_groq_ladder_strongest_first():
    """The strongest Groq models must appear first in the ladder."""
    assert gemini._GROQ_MODELS[0] == "qwen/qwen3-coder-480b"
    assert gemini._GROQ_MODELS[1] == "deepseek-r1"
    assert len(gemini._GROQ_MODELS) >= 3


def test_github_ladder_strongest_first():
    """The strongest GitHub models must appear first in the ladder."""
    assert gemini._GITHUB_MODELS[0] == "gpt-4.5-preview"
    assert gemini._GITHUB_MODELS[1] == "o3-pro"
    assert len(gemini._GITHUB_MODELS) >= 3


def test_github_key_from_config(monkeypatch):
    monkeypatch.setattr(
        "memory.config_manager.load_api_keys",
        lambda: {"github_api_key": "ghp_test_token"},
    )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert gemini.github_key() == "ghp_test_token"


def test_github_key_from_env(monkeypatch):
    monkeypatch.setattr("memory.config_manager.load_api_keys", lambda: {})
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_env_token")
    assert gemini.github_key() == "ghp_env_token"


def test_groq_call_returns_reply(monkeypatch):
    """When a Groq key is set and the API succeeds, _groq_call returns a _Reply."""
    monkeypatch.setattr(gemini, "groq_key", lambda: "gsk_test_key")

    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = "groq answer"

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    def _make_client(api_key=None, timeout=None):
        return fake_client

    _install_mock_module(monkeypatch, "groq", _make_client)

    reply = gemini._groq_call("hello")
    assert reply is not None
    assert reply.text == "groq answer"


def test_groq_call_returns_none_without_key(monkeypatch):
    monkeypatch.setattr(gemini, "groq_key", lambda: "")
    assert gemini._groq_call("hello") is None


def test_github_call_returns_reply(monkeypatch):
    """When a GitHub token is set and the API succeeds, _github_call returns a _Reply."""
    monkeypatch.setattr(gemini, "github_key", lambda: "ghp_test_token")

    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = "github answer"

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    _install_mock_module(monkeypatch, "openai", lambda **kwargs: fake_client)

    reply = gemini._github_call("hello")
    assert reply is not None
    assert reply.text == "github answer"


def test_github_call_returns_none_without_key(monkeypatch):
    monkeypatch.setattr(gemini, "github_key", lambda: "")
    assert gemini._github_call("hello") is None


def test_github_call_uses_models_github_ai_endpoint(monkeypatch):
    """GitHub calls must go to models.github.ai, not the old Red Hat endpoint."""
    monkeypatch.setattr(gemini, "github_key", lambda: "ghp_test_token")

    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = "github answer"
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp
    _install_mock_module(monkeypatch, "openai", lambda **kwargs: fake_client)

    gemini._github_call("hello")

    create_kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert create_kwargs["model"] == "gpt-4.5-preview"


def test_call_falls_back_to_groq(monkeypatch):
    """call() should use Groq when Gemini keys are unavailable and Groq succeeds."""
    # No Gemini keys configured.
    monkeypatch.setattr(gemini, "api_key", lambda key_num=1: "")
    monkeypatch.setattr(gemini, "ollama_available", lambda: False)
    monkeypatch.setattr(gemini, "groq_key", lambda: "gsk_test_key")
    monkeypatch.setattr(gemini, "github_key", lambda: "")

    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = "groq fallback"
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    _install_mock_module(monkeypatch, "groq", lambda api_key=None, timeout=None: fake_client)

    reply = gemini.call("test prompt", tier=gemini.FAST)
    assert reply is not None
    assert reply.text == "groq fallback"


def test_call_falls_back_to_github(monkeypatch):
    """call() should use GitHub when Gemini keys are unavailable and Groq is not configured."""
    monkeypatch.setattr(gemini, "api_key", lambda key_num=1: "")
    monkeypatch.setattr(gemini, "ollama_available", lambda: False)
    monkeypatch.setattr(gemini, "groq_key", lambda: "")
    monkeypatch.setattr(gemini, "github_key", lambda: "ghp_test_token")

    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = "github fallback"
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    _install_mock_module(monkeypatch, "openai", lambda **kwargs: fake_client)

    reply = gemini.call("test prompt", tier=gemini.FAST)
    assert reply is not None
    assert reply.text == "github fallback"


def test_task_model_ladder_respects_provider_availability(monkeypatch):
    """task_model_ladder should only include models from available providers."""
    monkeypatch.setattr(gemini, "_provider_status", lambda: {
        "gemini": {"available": True, "has_primary_key": True, "has_fallback_key": True},
        "groq": {"available": True},
        "github": {"available": True},
        "ollama": {"available": False},
    })
    monkeypatch.setattr(gemini, "_cooling", lambda m: False)

    ladder = gemini.task_model_ladder("coding")
    # Should contain models from all available providers
    assert len(ladder) > 0
    # Groq strongest model should be in the ladder
    assert "qwen/qwen3-coder-480b" in ladder
    # GitHub strongest model should be in the ladder
    assert "gpt-4.5-preview" in ladder


def test_task_model_ladder_excludes_unavailable_providers(monkeypatch):
    """When a provider has no key, its models should not appear."""
    monkeypatch.setattr(gemini, "_provider_status", lambda: {
        "gemini": {"available": False, "has_primary_key": False, "has_fallback_key": False},
        "groq": {"available": False},
        "github": {"available": True},
        "ollama": {"available": False},
    })
    monkeypatch.setattr(gemini, "github_key", lambda: "ghp_test_token")

    ladder = gemini.task_model_ladder("coding")
    # No Groq or Gemini models since they're unavailable
    assert "qwen/qwen3-coder-480b" not in ladder
    # But GitHub models should still be present
    assert "gpt-4.5-preview" in ladder


def test_select_model_for_task_returns_best_available(monkeypatch):
    """select_model_for_task should return a viable model+provider pair."""
    monkeypatch.setattr(gemini, "_provider_status", lambda: {
        "gemini": {"available": True, "has_primary_key": True, "has_fallback_key": True},
        "groq": {"available": False},
        "github": {"available": True},
        "ollama": {"available": False},
    })
    monkeypatch.setattr(gemini, "_cooling", lambda m: False)

    model, provider = gemini.select_model_for_task("coding")
    assert model is not None
    assert provider == "gemini"  # Gemini available, so it's tried first


def test_select_model_prefers_provider_order(monkeypatch):
    """If Gemini is unavailable, should fall through to next provider in order."""
    monkeypatch.setattr(gemini, "_provider_status", lambda: {
        "gemini": {"available": False, "has_primary_key": False, "has_fallback_key": False},
        "groq": {"available": True},
        "github": {"available": True},
        "ollama": {"available": False},
    })
    monkeypatch.setattr(gemini, "_cooling", lambda m: False)

    model, provider = gemini.select_model_for_task("coding")
    # CODE tier has groq first in provider_order
    assert provider == "groq"
    assert model == "qwen/qwen3-coder-480b"


def test_get_task_routes_detailed_returns_task_info():
    """get_task_routes_detailed should return structured route data."""
    routes = gemini.get_task_routes_detailed()
    assert len(routes) == len(gemini.TASK_TIERS)
    for route in routes:
        assert "task" in route
        assert "label" in route
        assert "tier" in route
        assert "models" in route
        assert "full_ladder" in route
        assert "best_model" in route
        assert "best_provider" in route


def test_save_and_get_task_model_order(tmp_path, monkeypatch):
    """User overrides for model order should be saved and retrieved."""
    # Redirect config path to a temp file
    monkeypatch.setattr(
        "core.paths.get_config_path",
        lambda name: tmp_path / name,
    )
    monkeypatch.setattr(
        "memory.config_manager.load_api_keys",
        lambda: {},
    )

    gemini.reload_model_ladder()

    custom_order = ["deepseek-r1", "qwen/qwen3-coder-480b", "llama-3.3-70b"]
    result = gemini.save_task_model_order("coding", "groq", custom_order)
    assert result is True

    retrieved = gemini.get_task_model_order("coding", "groq")
    assert retrieved == custom_order


def test_get_available_models_returns_config():
    """get_available_models should return models grouped by provider."""
    models = gemini.get_available_models()
    assert "gemini" in models
    assert "github" in models
    assert "groq" in models
    assert len(models["gemini"]["models"]) > 0
    assert len(models["github"]["models"]) > 0
    assert len(models["groq"]["models"]) > 0
