"""Unit tests for GitHub discovery, inspection and safe path handling."""

from __future__ import annotations

import pytest

from actions import github_integrate, github_scout


class _Response:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise github_scout.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_scout_builds_filters_and_reports(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response(
            {
                "items": [
                    {
                        "full_name": "owner/repo",
                        "stargazers_count": 1234,
                        "updated_at": "2026-01-02T03:04:05Z",
                        "description": "line one\nline two",
                        "html_url": "https://github.com/owner/repo",
                    }
                ]
            }
        )

    monkeypatch.setattr(github_scout.requests, "get", fake_get)

    result = github_scout.github_scout(
        {"query": "desktop assistant", "language": "Python", "min_stars": 100}
    )

    assert "language:Python" in captured["params"]["q"]
    assert "stars:>=100" in captured["params"]["q"]
    assert "owner/repo" in result
    assert "line one line two" in result


def test_scout_uses_github_token_from_environment(monkeypatch):
    captured = {}

    def fake_get(_url, **kwargs):
        captured.update(kwargs)
        return _Response({"items": []})

    # The machine running the tests may have a real token saved by JARVIS;
    # environment handling must be verified without it leaking into the run.
    monkeypatch.setattr("core.paths.load_config", lambda _name: {})
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(github_scout.requests, "get", fake_get)

    github_scout.github_scout({"query": "desktop assistant"})

    assert captured["headers"]["Authorization"] == "Bearer test-token"


def test_scout_prefers_token_saved_in_jarvis_settings(monkeypatch):
    captured = {}

    def fake_get(_url, **kwargs):
        captured.update(kwargs)
        return _Response({"items": []})

    monkeypatch.setenv("GITHUB_TOKEN", "environment-token")
    monkeypatch.setattr(
        "core.paths.load_config", lambda _name: {"github_token": "jarvis-token"}
    )
    monkeypatch.setattr(github_scout.requests, "get", fake_get)

    github_scout.github_scout({"query": "desktop assistant"})

    assert captured["headers"]["Authorization"] == "Bearer jarvis-token"


def test_scout_omits_authorization_without_github_token(monkeypatch):
    captured = {}

    def fake_get(_url, **kwargs):
        captured.update(kwargs)
        return _Response({"items": []})

    # Ignore a real token saved in the local JARVIS config: this test covers
    # the anonymous-request path only.
    monkeypatch.setattr("core.paths.load_config", lambda _name: {})
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(github_scout.requests, "get", fake_get)

    github_scout.github_scout({"query": "desktop assistant"})

    assert "Authorization" not in captured["headers"]


def test_scout_survives_invalid_min_stars(monkeypatch):
    monkeypatch.setattr(
        github_scout.requests, "get", lambda *a, **k: _Response({"items": []})
    )

    result = github_scout.github_scout({"query": "desktop assistant", "min_stars": "many"})

    assert "не найдено" in result


def test_scout_rejects_short_query():
    assert "2 до 160" in github_scout.github_scout({"query": "x"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("owner/repo", "owner/repo"),
        ("https://github.com/owner/repo", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("https://gitlab.com/owner/repo", None),
        ("https://github.com/owner", None),
        ("owner/repo/extra", None),
        ("", None),
    ],
)
def test_normalize_repo(raw, expected):
    assert github_integrate._normalize_repo(raw) == expected


@pytest.mark.parametrize("path", ["../secret", "docs/../../secret"])
def test_read_raw_rejects_unsafe_paths(monkeypatch, path):
    def fail_get(*_args, **_kwargs):
        raise AssertionError("network must not be touched for unsafe paths")

    monkeypatch.setattr("requests.get", fail_get)

    assert github_integrate._read_raw("owner/repo", path) is None


def test_integrate_reads_reviewed_files_without_clone(monkeypatch, tmp_path):
    monkeypatch.setattr(github_integrate, "get_app_dir", lambda: tmp_path)
    seen = []

    def fake_read(repo, path):
        seen.append((repo, path))
        return f"content of {path}"

    monkeypatch.setattr(github_integrate, "_read_raw", fake_read)
    monkeypatch.setattr(
        github_integrate,
        "_clone",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("clone disabled")),
    )

    result = github_integrate.github_integrate(
        {
            "repo": "https://github.com/owner/repo",
            "goal": "test",
            "files": ["README.md"],
            "clone": False,
        }
    )

    assert "owner/repo" in result
    assert "content of README.md" in result
    assert "clone=false" in result
    assert seen == [("owner/repo", "README.md")]


def test_integrate_clones_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(github_integrate, "get_app_dir", lambda: tmp_path)
    cloned = []

    def fake_clone(repo, destination):
        cloned.append((repo, destination))
        return "cloned"

    monkeypatch.setattr(github_integrate, "_clone", fake_clone)
    monkeypatch.setattr(github_integrate, "_read_raw", lambda *a: None)

    result = github_integrate.github_integrate(
        {"repo": "owner/repo", "files": ["README.md"]}
    )

    assert cloned == [("owner/repo", tmp_path / "vendor_scout" / "owner-repo")]
    assert "Локальная копия" in result


def test_integrate_requires_repository():
    assert "owner/name" in github_integrate.github_integrate({})
