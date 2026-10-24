from core.mcp_registry import MCPRegistry, _is_method_not_found, _schema_for_gemini, _tool_name


def test_schema_conversion_and_depth_limit():
    converted = _schema_for_gemini({
        "type": "object",
        "properties": {
            "names": {"type": "array", "items": {"type": "string"}},
            "enabled": {"type": ["boolean", "null"]},
        },
        "required": ["names"],
    })
    assert converted["type"] == "OBJECT"
    assert converted["properties"]["names"]["items"]["type"] == "STRING"
    assert converted["properties"]["enabled"]["nullable"] is True
    assert converted["required"] == ["names"]

    deep = {"type": "string"}
    for _ in range(10):
        deep = {"type": "array", "items": deep}
    node = _schema_for_gemini(deep)
    for _ in range(8):
        node = node["items"]
    assert node["type"] == "STRING"
    assert "depth limit" in node["description"]


def test_external_http_mcp_requires_tls():
    registry = object.__new__(MCPRegistry)
    try:
        registry._server_params({"transport": "http", "url": "http://example.com/mcp"})
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("remote plain HTTP must be rejected")

    assert registry._server_params({"transport": "http", "url": "https://example.com/mcp"}) == "https://example.com/mcp"
    assert registry._server_params({"transport": "http", "url": "http://localhost:9000/mcp"}) == "http://localhost:9000/mcp"


def test_tool_names_are_bounded_and_long_names_keep_distinct_suffixes():
    first = _tool_name("server", "x" * 100 + "a")
    second = _tool_name("server", "x" * 100 + "b")
    assert len(first) <= 64
    assert len(second) <= 64
    assert first != second
    assert first.startswith("mcp_server_")


def test_method_not_found_is_detected_inside_exception_group():
    error = ExceptionGroup("outer", [RuntimeError("JSON-RPC -32601 Method not found")])
    assert _is_method_not_found(error)
    assert not _is_method_not_found(RuntimeError("connection refused"))
