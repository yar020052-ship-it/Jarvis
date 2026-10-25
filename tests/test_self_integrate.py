"""Safety tests for the reviewed-file installer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from actions import self_integrate as installer


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "get_app_dir", lambda: tmp_path)
    return tmp_path


def _health(monkeypatch, ok: bool) -> None:
    from autonomy import health

    monkeypatch.setattr(health, "run_health", lambda: SimpleNamespace(ok=ok))


def test_installs_new_file(project, monkeypatch):
    _health(monkeypatch, True)
    source = project / "vendor_scout" / "demo" / "probe.md"
    source.parent.mkdir(parents=True)
    source.write_text("probe", encoding="utf-8")

    message = installer.self_integrate(
        {"source": "vendor_scout/demo/probe.md", "target": "docs/probe.md"}
    )

    assert "Установлен" in message
    assert (project / "docs" / "probe.md").read_text(encoding="utf-8") == "probe"


def test_removes_new_file_when_health_fails(project, monkeypatch):
    _health(monkeypatch, False)
    source = project / "vendor_scout" / "demo" / "broken.py"
    source.parent.mkdir(parents=True)
    source.write_text("def broken(:\n", encoding="utf-8")

    message = installer.self_integrate(
        {"source": "vendor_scout/demo/broken.py", "target": "actions/broken.py"}
    )

    assert "откачено" in message
    assert not (project / "actions" / "broken.py").exists()


def test_restores_previous_file_when_health_fails(project, monkeypatch):
    _health(monkeypatch, False)
    target = project / "actions" / "existing.py"
    target.parent.mkdir(parents=True)
    target.write_text("original\n", encoding="utf-8")
    source = project / "vendor_scout" / "demo" / "replacement.py"
    source.parent.mkdir(parents=True)
    source.write_text("broken(\n", encoding="utf-8")

    message = installer.self_integrate(
        {
            "source": "vendor_scout/demo/replacement.py",
            "target": "actions/existing.py",
            "overwrite": True,
        }
    )

    assert "откачено" in message
    assert target.read_text(encoding="utf-8") == "original\n"


def test_refuses_overwrite_without_flag(project, monkeypatch):
    _health(monkeypatch, True)
    target = project / "docs" / "probe.md"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")
    source = project / "vendor_scout" / "demo" / "probe.md"
    source.parent.mkdir(parents=True)
    source.write_text("new", encoding="utf-8")

    message = installer.self_integrate(
        {"source": "vendor_scout/demo/probe.md", "target": "docs/probe.md"}
    )

    assert "уже существует" in message
    assert target.read_text(encoding="utf-8") == "old"


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("main.py", "запрещено"),
        ("../escape.md", "Укажите source и target"),
        ("secrets/keys.txt", "Можно устанавливать только"),
    ],
)
def test_rejects_dangerous_targets(project, monkeypatch, target, reason):
    _health(monkeypatch, True)
    source = project / "vendor_scout" / "demo" / "probe.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n", encoding="utf-8")

    message = installer.self_integrate(
        {"source": "vendor_scout/demo/probe.py", "target": target, "overwrite": True}
    )

    assert reason in message
