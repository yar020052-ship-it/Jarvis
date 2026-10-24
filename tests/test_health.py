from types import SimpleNamespace

from autonomy import health


def test_health_syntax_scan_covers_core_ui_main_and_tests(tmp_path, monkeypatch):
    for rel in (
        "main.py",
        "ui.py",
        "actions/action.py",
        "plugins/plugin.py",
        "core/module.py",
        "dashboard/server.py",
        "autonomy/task.py",
        "tests/test_example.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value = 1\n", encoding="utf-8")

    monkeypatch.setattr(health, "BASE_DIR", tmp_path)
    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    good = health.run_health()
    assert good.checks[0].ok
    assert good.checks[0].detail == "файлов: 8"

    (tmp_path / "core" / "module.py").write_text("def incomplete(\n", encoding="utf-8")
    bad = health.run_health()
    assert not bad.checks[0].ok
    assert "core/module.py" in bad.checks[0].detail
