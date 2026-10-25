"""Tests for Gemini one-shot configuration and key cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import gemini


@pytest.fixture(autouse=True)
def reset_key_cache(monkeypatch):
    monkeypatch.setattr(gemini, "_cached_key", None, raising=False)
    monkeypatch.setattr(gemini, "_cached_key_signature", None, raising=False)


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
