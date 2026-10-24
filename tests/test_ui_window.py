from __future__ import annotations

import os


def test_main_window_resize_controls_splitter_and_settings_overlay(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import ui

    monkeypatch.setattr(ui, "_read_full_config", lambda: {})
    monkeypatch.setattr(ui, "_save_cfg", lambda **_kwargs: None)
    app = ui.QApplication.instance() or ui.QApplication([])
    window = ui.MainWindow("")
    window.show()
    app.processEvents()

    window.resize(1050, 720)
    app.processEvents()
    assert (window.width(), window.height()) == (1050, 720)
    assert (window.minimumWidth(), window.minimumHeight()) == (ui._MIN_W, ui._MIN_H)
    assert len(window._resize_handles) == 8
    assert all(handle.isVisible() for handle in window._resize_handles.values())

    before = window._body_split.sizes()
    window._body_split.setSizes([230, 510, 260])
    app.processEvents()
    after = window._body_split.sizes()
    assert after[0] != before[0]

    window._toggle_maximized()
    app.processEvents()
    assert window.isMaximized()
    assert not any(handle.isVisible() for handle in window._resize_handles.values())
    window._toggle_maximized()
    app.processEvents()
    assert not window.isMaximized()
    assert all(handle.isVisible() for handle in window._resize_handles.values())

    window._open_plugin_settings()
    app.processEvents()
    overlay = window._plugin_settings_overlay
    central = window.centralWidget()
    face_center = window.hud.mapTo(central, window.hud.rect().center())
    assert overlay.isVisible()
    assert central.rect().contains(overlay.geometry())
    assert overlay.geometry().contains(face_center)

    # The real UI flushes chat history on close; keep this isolated test from
    # writing to the user's local conversation history.
    if window._chat is not None and hasattr(window._chat, "flush_history"):
        window._chat.flush_history = lambda: None
    window.close()
    app.processEvents()


def test_event_log_button_finds_journal_actions(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import ui

    monkeypatch.setattr(ui, "_read_full_config", lambda: {})
    monkeypatch.setattr(ui, "_save_cfg", lambda **_kwargs: None)
    app = ui.QApplication.instance() or ui.QApplication([])
    window = ui.MainWindow("")
    if window._chat is not None and hasattr(window._chat, "flush_history"):
        window._chat.flush_history = lambda: None

    window._event_log.requested.emit()
    app.processEvents()

    assert window._palette is not None
    titles = [item[0] for item in window._palette._shown]
    assert "Скопировать журнал" in titles
    assert "Очистить журнал" in titles
    assert "Ничего не найдено" not in titles

    window._palette.close_palette()
    window.close()
    app.processEvents()


def test_activity_error_counter_recognizes_recovery_failures():
    from types import SimpleNamespace

    import ui

    assert ui._is_error_event("SYS: Не удалось восстановить диалог")
    assert ui._is_error_event("Tool 'system_status' failed: TimeoutError")
    assert not ui._is_error_event("SYS: JARVIS в сети")

    window = SimpleNamespace(
        _activity_error_count=0,
        _activity_error_events=[],
        _refresh_activity_summary=lambda: None,
    )
    ui.MainWindow._count_activity_error(window, "SYS: Не удалось восстановить диалог")
    assert window._activity_error_count == 1
    assert window._activity_error_events == ["SYS: Не удалось восстановить диалог"]


def test_activity_summary_labels_journal_messages_not_broken_modules():
    from types import SimpleNamespace

    import ui

    class Label:
        text = ""
        tooltip = ""

        def setText(self, value):
            self.text = value

        def setToolTip(self, value):
            self.tooltip = value

    chip = Label()
    status = Label()
    window = SimpleNamespace(
        _activity_chip=chip,
        _activity_status_lbl=status,
        _activity_error_count=4,
        _tasks=[{"done": False}, {"done": True}],
    )

    ui.MainWindow._refresh_activity_summary(window)

    assert chip.text == "4 в журнале · 1 задача"
    assert status.text == "Сообщений с ошибкой в журнале за сессию: 4"
    assert "не количество активных неисправностей модулей" in chip.tooltip
