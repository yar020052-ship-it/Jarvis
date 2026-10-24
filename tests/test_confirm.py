from core import confirm


def test_request_without_interface_returns_refusal():
    confirm.bind(None, None)
    result = confirm.request("key", "title", "detail", lambda: "done")
    assert "not available" in result
    assert confirm.pending_title() == ""


def test_resolve_without_pending_is_noop():
    confirm.resolve(True)
    confirm.resolve(False)