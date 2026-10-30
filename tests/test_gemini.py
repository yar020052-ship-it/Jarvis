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


def test_call_falls_back_to_groq(monkeypatch):
    """call() should use Groq when Gemini keys are unavailable and Groq succeeds."""
    # No Gemini keys configured.
    monkeypatch.setattr(gemini, "api_key", lambda key_num=1: "")
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
