from __future__ import annotations

import os


def _chat_widget(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import ui
    from PyQt6.QtCore import QPoint, QRect
    from PyQt6.QtTest import QTest

    monkeypatch.setattr(ui.ChatWidget, "save_history", lambda _self: None)
    app = ui.QApplication.instance() or ui.QApplication([])
    widget = ui.ChatWidget()
    widget._messages.clear()
    widget._render_history()
    widget.resize(460, 320)
    widget.show()
    app.processEvents()
    QTest.qWait(20)
    return app, widget, QTest


def _close_widget(app, widget):
    widget.close()
    app.processEvents()


def test_chat_widget_has_sticky_scroll_controls(monkeypatch):
    app, widget, _ = _chat_widget(monkeypatch)

    assert hasattr(widget, "_scroll_down_btn")
    assert hasattr(widget, "is_at_bottom")
    assert callable(widget.scroll_to_bottom)
    assert widget.is_at_bottom() is True

    _close_widget(app, widget)


def test_scroll_to_bottom_resets_stickiness(monkeypatch):
    app, widget, _ = _chat_widget(monkeypatch)

    widget._stick_to_bottom = False
    widget.scroll_to_bottom()
    app.processEvents()

    assert widget._stick_to_bottom is True

    _close_widget(app, widget)


def test_add_message_preserves_non_bottom_position(monkeypatch):
    app, widget, QTest = _chat_widget(monkeypatch)
    for i in range(35):
        widget.add_message("jarvis", f"message {i}: " + "long content " * 8)
    widget.scroll_to_bottom()
    app.processEvents()
    QTest.qWait(30)
    bar = widget._scroll.verticalScrollBar()
    bar.setValue(max(0, bar.maximum() - 150))
    app.processEvents()
    assert widget.is_at_bottom() is False
    before = bar.value()

    widget.add_message("jarvis", "a new message that should not force scroll")
    app.processEvents()
    QTest.qWait(20)

    assert widget._stick_to_bottom is False
    assert bar.value() >= before
    assert widget.is_at_bottom() is False
    assert widget._scroll_down_btn.isVisible()

    _close_widget(app, widget)


def test_hide_show_and_overview_toggle_preserve_read_position(monkeypatch):
    app, widget, QTest = _chat_widget(monkeypatch)
    for i in range(35):
        widget.add_message("jarvis", f"message {i}: " + "long content " * 8)
    widget.scroll_to_bottom()
    app.processEvents()
    QTest.qWait(30)

    bar = widget._scroll.verticalScrollBar()
    bar.setValue(max(0, bar.maximum() // 2))
    app.processEvents()
    QTest.qWait(130)
    assert bar.value() != bar.maximum()
    assert not widget.is_at_bottom()
    original = bar.value()

    widget.hide()
    app.processEvents()
    widget.show()
    app.processEvents()
    assert not widget.is_at_bottom()
    assert abs(bar.value() - original) <= 2

    widget.set_overview_mode(True)
    app.processEvents()
    widget.set_overview_mode(False)
    app.processEvents()
    assert not widget.is_at_bottom()
    assert abs(bar.value() - original) <= 2

    _close_widget(app, widget)


def test_bubble_width_never_exceeds_narrow_viewport(monkeypatch):
    import ui

    app, widget, _ = _chat_widget(monkeypatch)
    widget.resize(120, 300)
    widget.add_message("jarvis", "a long message " * 20)
    app.processEvents()

    viewport_width = widget._scroll.viewport().width()
    bubbles = [
        widget._lay.itemAt(i).widget()
        for i in range(widget._lay.count())
        if isinstance(widget._lay.itemAt(i).widget(), ui.ChatBubble)
    ]
    assert viewport_width > 0
    assert bubbles
    assert all(bubble.maximumWidth() <= viewport_width for bubble in bubbles)

    _close_widget(app, widget)


def test_scroll_to_latest_does_not_land_in_trailing_blank_space(monkeypatch):
    from PyQt6.QtCore import QPoint, QRect
    import ui

    app, widget, QTest = _chat_widget(monkeypatch)
    for i in range(4):
        widget.add_message("jarvis", f"message {i}")
    app.processEvents()
    QTest.qWait(20)
    last = next(
        widget._lay.itemAt(i).widget()
        for i in reversed(range(widget._lay.count()))
        if isinstance(widget._lay.itemAt(i).widget(), ui.ChatBubble)
    )

    # Simulate an oversized scroll-area content widget (for example after its
    # layout has retained extra height). Scrolling to scrollbar.maximum() then
    # lands below the final message and shows an apparently empty conversation.
    widget._container.setMinimumHeight(widget._container.height() + 500)
    widget._container.updateGeometry()
    app.processEvents()
    QTest.qWait(20)
    widget.scroll_to_bottom()
    app.processEvents()

    viewport = widget._scroll.viewport()
    last_rect = QRect(last.mapTo(viewport, QPoint(0, 0)), last.size())
    assert last_rect.intersects(viewport.rect())
    assert widget.is_at_bottom()
    assert not widget._scroll_down_btn.isVisible()

    _close_widget(app, widget)


def test_manual_scroll_snaps_to_whole_bubbles(monkeypatch):
    from PyQt6.QtCore import QPoint, QRect
    import ui

    app, widget, QTest = _chat_widget(monkeypatch)
    for i in range(16):
        widget.add_message(
            "jarvis",
            f"Message {i}. " + ("A longer line that wraps in the chat. " * (i % 4 + 1)),
        )
    app.processEvents()
    QTest.qWait(30)
    viewport = widget._scroll.viewport()
    bubbles = [
        widget._lay.itemAt(i).widget()
        for i in range(widget._lay.count())
        if isinstance(widget._lay.itemAt(i).widget(), ui.ChatBubble)
    ]
    middle = bubbles[7]
    middle_top = middle.mapTo(widget._container, QPoint(0, 0)).y()
    widget._scroll.verticalScrollBar().setValue(middle_top + middle.height() // 2)
    app.processEvents()

    widget._snap_scroll_to_bubble_boundary()
    app.processEvents()

    viewport_rect = viewport.rect()
    visible = []
    for bubble in bubbles:
        rect = QRect(bubble.mapTo(viewport, QPoint(0, 0)), bubble.size())
        if rect.intersects(viewport_rect):
            visible.append(rect)
            # Snapping must never leave the reader inside the middle of a
            # message: every visible bubble starts at or below the top edge.
            assert rect.top() >= 0
    assert visible
    # Gaps between bubbles are ~8 px while the viewport is ~235 px tall, so
    # for most scroll positions the bottom edge cannot land exactly in a gap.
    # At most the last visible bubble may be clipped there — that is normal
    # messenger behaviour, unlike the old snap which jumped to the very end.
    assert sum(1 for rect in visible if rect.bottom() >= viewport_rect.height()) <= 1

    _close_widget(app, widget)


def test_main_window_dialog_keeps_bubbles_after_scroll_and_mode_switch(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import ui
    from PyQt6.QtCore import QPoint, QRect
    from PyQt6.QtTest import QTest

    monkeypatch.setattr(ui, "_read_full_config", lambda: {})
    monkeypatch.setattr(ui, "_save_cfg", lambda **_kwargs: None)
    monkeypatch.setattr(ui.ChatWidget, "load_history", lambda _self: None)
    monkeypatch.setattr(ui.ChatWidget, "save_history", lambda _self: None)
    app = ui.QApplication.instance() or ui.QApplication([])
    window = ui.MainWindow("")
    window.resize(1360, 860)
    window.show()
    app.processEvents()
    chat = window._chat
    chat._messages.extend(
        [("user", f"Проверка диалога {i}") for i in range(195)]
        + [("jarvis", "Последнее сообщение должно оставаться видимым.")]
    )

    window._header_show_dialog()
    app.processEvents()
    QTest.qWait(30)

    def visible_bubbles():
        return [
            chat._lay.itemAt(i).widget()
            for i in range(chat._lay.count())
            if isinstance(chat._lay.itemAt(i).widget(), ui.ChatBubble)
            and chat._lay.itemAt(i).widget().isVisible()
        ]

    def bubble_intersects_viewport():
        viewport = chat._scroll.viewport()
        visible = viewport.rect()
        return any(
            QRect(bubble.mapTo(viewport, QPoint(0, 0)), bubble.size()).intersects(visible)
            for bubble in visible_bubbles()
        )

    def last_bubble_intersects_viewport():
        viewport = chat._scroll.viewport()
        bubble = visible_bubbles()[-1]
        return QRect(bubble.mapTo(viewport, QPoint(0, 0)), bubble.size()).intersects(
            viewport.rect()
        )

    assert len(visible_bubbles()) == 196
    assert bubble_intersects_viewport()
    chat.scroll_to_bottom()
    app.processEvents()
    QTest.qWait(20)
    assert len(visible_bubbles()) == 196
    assert bubble_intersects_viewport()
    assert last_bubble_intersects_viewport()

    window._header_show_overview()
    app.processEvents()
    window._header_show_dialog()
    app.processEvents()
    QTest.qWait(30)
    assert len(visible_bubbles()) == 196
    assert bubble_intersects_viewport()
    assert last_bubble_intersects_viewport()

    window.close()
    app.processEvents()
