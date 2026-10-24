"""Тесты распознавания мёртвого resume-handle Live-сессии."""

from core.live_recovery import is_dead_resume_handle


def test_websocket_1008_already_exists_is_dead_handle():
    error = "1008 None. {'error': {'code': 1008, 'message': 'Session already exists'}}"
    assert is_dead_resume_handle(error)


def test_expired_handle_is_dead():
    assert is_dead_resume_handle(RuntimeError("resumption handle expired"))
    assert is_dead_resume_handle("INVALID_ARGUMENT: handle not found")


def test_not_found_variants_are_dead():
    assert is_dead_resume_handle("NOT_FOUND")
    assert is_dead_resume_handle("session not found")


def test_quota_error_is_not_a_dead_handle():
    assert not is_dead_resume_handle("429 RESOURCE_EXHAUSTED")


def test_ordinary_errors_are_not_dead_handles():
    assert not is_dead_resume_handle("no JSON in model reply")
    assert not is_dead_resume_handle("dashboard: fastapi/uvicorn not installed")
