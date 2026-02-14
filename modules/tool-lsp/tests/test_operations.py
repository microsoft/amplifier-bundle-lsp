"""Tests for operations.py — Task 1, Task 2, and Task 7 changes."""

import pytest

from amplifier_module_tool_lsp.operations import (
    LspOperations,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


class FakeServer:
    """Fake LspServer that records notifications sent to it."""

    def __init__(self):
        self.language = "python"
        self.notifications: list[tuple[str, dict]] = []

    async def notify(self, method: str, params: dict):
        self.notifications.append((method, params))

    async def request(self, method: str, params: dict):
        return None


# ── Step 1.7: Track open documents ───────────────────────────────────────────


class TestDocumentTracking:
    """_open_document should track documents and avoid redundant didOpen."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_open_documents_tracking_exists(self, ops):
        """LspOperations should have _open_documents dict."""
        assert hasattr(ops, "_open_documents")
        assert isinstance(ops._open_documents, dict)

    def test_doc_versions_tracking_exists(self, ops):
        """LspOperations should have _doc_versions dict."""
        assert hasattr(ops, "_doc_versions")
        assert isinstance(ops._doc_versions, dict)

    @pytest.mark.asyncio
    async def test_first_open_sends_did_open(self, ops, tmp_path):
        """First call to _open_document should send textDocument/didOpen."""
        server = FakeServer()
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._open_document(server, str(py_file))

        assert len(server.notifications) == 1
        method, params = server.notifications[0]
        assert method == "textDocument/didOpen"
        assert params["textDocument"]["version"] == 1
        assert params["textDocument"]["text"] == "x = 1\n"

    @pytest.mark.asyncio
    async def test_second_open_unchanged_skips(self, ops, tmp_path):
        """Second call with unchanged file should send nothing."""
        server = FakeServer()
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._open_document(server, str(py_file))
        server.notifications.clear()

        await ops._open_document(server, str(py_file))
        assert len(server.notifications) == 0, "Should skip unchanged file"

    @pytest.mark.asyncio
    async def test_second_open_changed_sends_did_change(self, ops, tmp_path):
        """Second call after file changed should send textDocument/didChange."""
        server = FakeServer()
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._open_document(server, str(py_file))
        server.notifications.clear()

        # Modify the file
        py_file.write_text("x = 2\n")

        await ops._open_document(server, str(py_file))

        assert len(server.notifications) == 1
        method, params = server.notifications[0]
        assert method == "textDocument/didChange"
        assert params["textDocument"]["version"] == 2

    @pytest.mark.asyncio
    async def test_version_increments(self, ops, tmp_path):
        """Version should increment on each change."""
        server = FakeServer()
        py_file = tmp_path / "test.py"

        py_file.write_text("v1\n")
        await ops._open_document(server, str(py_file))

        py_file.write_text("v2\n")
        await ops._open_document(server, str(py_file))

        py_file.write_text("v3\n")
        await ops._open_document(server, str(py_file))

        # Should have: didOpen(v=1), didChange(v=2), didChange(v=3)
        assert len(server.notifications) == 3
        assert server.notifications[0][1]["textDocument"]["version"] == 1
        assert server.notifications[1][1]["textDocument"]["version"] == 2
        assert server.notifications[2][1]["textDocument"]["version"] == 3

    @pytest.mark.asyncio
    async def test_did_change_contains_full_content(self, ops, tmp_path):
        """didChange should use full content replacement."""
        server = FakeServer()
        py_file = tmp_path / "test.py"

        py_file.write_text("original\n")
        await ops._open_document(server, str(py_file))
        server.notifications.clear()

        py_file.write_text("modified\n")
        await ops._open_document(server, str(py_file))

        method, params = server.notifications[0]
        assert method == "textDocument/didChange"
        # Full replacement means contentChanges has one entry with just text
        changes = params["contentChanges"]
        assert len(changes) == 1
        assert changes[0]["text"] == "modified\n"

    @pytest.mark.asyncio
    async def test_different_files_tracked_independently(self, ops, tmp_path):
        """Each file should be tracked independently."""
        server = FakeServer()
        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        file_a.write_text("a = 1\n")
        file_b.write_text("b = 1\n")

        await ops._open_document(server, str(file_a))
        await ops._open_document(server, str(file_b))

        # Both should have sent didOpen
        assert len(server.notifications) == 2
        assert server.notifications[0][0] == "textDocument/didOpen"
        assert server.notifications[1][0] == "textDocument/didOpen"


# ── Step (from plan): _uri_to_path utility ───────────────────────────────────


class TestUriToPath:
    """_uri_to_path should convert file:// URIs to filesystem paths."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_simple_path(self, ops):
        result = ops._uri_to_path("file:///home/user/test.py")
        assert result == "/home/user/test.py"

    def test_encoded_spaces(self, ops):
        result = ops._uri_to_path("file:///home/user/my%20project/test.py")
        assert result == "/home/user/my project/test.py"

    def test_encoded_special_chars(self, ops):
        result = ops._uri_to_path("file:///home/user/%23special/test.py")
        assert result == "/home/user/#special/test.py"


# ── Task 7: _truncate_custom_result ──────────────────────────────────────────


class TestTruncateCustomResult:
    """_truncate_custom_result should truncate long string values in dicts."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_short_strings_pass_through(self, ops):
        """Short string values should not be truncated."""
        result = ops._truncate_custom_result(
            {"name": "hello", "value": "short"}, "test/method"
        )
        assert result["name"] == "hello"
        assert result["value"] == "short"
        assert result["_truncated"] is False
        assert result["_method"] == "test/method"

    def test_long_string_truncated(self, ops):
        """String values longer than max_str_len should be truncated."""
        long_value = "x" * 20000
        result = ops._truncate_custom_result(
            {"expansion": long_value}, "rust-analyzer/expandMacro"
        )
        assert len(result["expansion"]) < 20000
        assert "truncated from 20000 chars" in result["expansion"]
        assert result["_truncated"] is True
        assert result["_method"] == "rust-analyzer/expandMacro"

    def test_custom_max_str_len(self, ops):
        """max_str_len parameter should control truncation threshold."""
        result = ops._truncate_custom_result(
            {"data": "a" * 100}, "test/method", max_str_len=50
        )
        assert len(result["data"]) < 100
        assert "truncated from 100 chars" in result["data"]
        assert result["_truncated"] is True

    def test_non_string_values_pass_through(self, ops):
        """Non-string values (ints, lists, dicts) should not be affected."""
        result = ops._truncate_custom_result(
            {"count": 42, "items": [1, 2, 3], "nested": {"a": 1}},
            "test/method",
        )
        assert result["count"] == 42
        assert result["items"] == [1, 2, 3]
        assert result["nested"] == {"a": 1}
        assert result["_truncated"] is False

    def test_mixed_short_and_long(self, ops):
        """Only long strings should be truncated; short ones preserved."""
        result = ops._truncate_custom_result(
            {"short": "ok", "long": "z" * 20000},
            "test/method",
        )
        assert result["short"] == "ok"
        assert len(result["long"]) < 20000
        assert result["_truncated"] is True

    def test_method_tag_added(self, ops):
        """Result should always contain _method tag."""
        result = ops._truncate_custom_result({}, "my/method")
        assert result["_method"] == "my/method"

    def test_empty_dict(self, ops):
        """Empty dict should just get _method and _truncated fields."""
        result = ops._truncate_custom_result({}, "test/method")
        assert result["_method"] == "test/method"
        assert result["_truncated"] is False


# ── Task 7: _op_customRequest ────────────────────────────────────────────────


class CustomFakeServer:
    """Fake server for customRequest tests — configurable return values."""

    def __init__(self, response=None, error=None):
        self.language = "python"
        self.notifications: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, dict]] = []
        self._response = response
        self._error = error

    async def notify(self, method: str, params: dict):
        self.notifications.append((method, params))

    async def request(self, method: str, params: dict):
        self.requests.append((method, params))
        if self._error:
            raise self._error
        return self._response


class TestOpCustomRequest:
    """_op_customRequest should send arbitrary LSP methods."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_missing_custom_method_returns_error(self, ops):
        """Should return error dict when customMethod is missing."""
        server = CustomFakeServer()
        result = await ops._op_customRequest(server)
        assert "error" in result
        assert "customMethod" in result["error"]

    @pytest.mark.asyncio
    async def test_dict_result(self, ops, tmp_path):
        """Dict result should be processed through _truncate_custom_result."""
        server = CustomFakeServer(response={"name": "expanded", "code": "impl Debug"})
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        result = await ops._op_customRequest(
            server,
            file_path=str(py_file),
            customMethod="rust-analyzer/expandMacro",
        )
        assert result["name"] == "expanded"
        assert result["code"] == "impl Debug"
        assert result["_method"] == "rust-analyzer/expandMacro"
        assert result["_truncated"] is False

    @pytest.mark.asyncio
    async def test_list_result(self, ops, tmp_path):
        """List result should use _truncate_results."""
        items = [{"name": f"dep{i}"} for i in range(5)]
        server = CustomFakeServer(response=items)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        result = await ops._op_customRequest(
            server,
            file_path=str(py_file),
            customMethod="rust-analyzer/fetchDependencyList",
        )
        assert "results" in result
        assert result["total_count"] == 5

    @pytest.mark.asyncio
    async def test_null_result(self, ops, tmp_path):
        """None result should return structured null result."""
        server = CustomFakeServer(response=None)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        result = await ops._op_customRequest(
            server,
            file_path=str(py_file),
            customMethod="rust-analyzer/reloadWorkspace",
        )
        assert result["result"] is None
        assert result["_method"] == "rust-analyzer/reloadWorkspace"
        assert "null result" in result["message"]

    @pytest.mark.asyncio
    async def test_scalar_result(self, ops, tmp_path):
        """Scalar result (e.g., string, int) should pass through."""
        server = CustomFakeServer(response=42)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        result = await ops._op_customRequest(
            server,
            file_path=str(py_file),
            customMethod="test/scalarMethod",
        )
        assert result["result"] == 42
        assert result["_method"] == "test/scalarMethod"
        assert "completed" in result["message"]

    @pytest.mark.asyncio
    async def test_auto_inject_text_document(self, ops, tmp_path):
        """When file_path provided, textDocument should be auto-injected."""
        server = CustomFakeServer(response=None)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._op_customRequest(
            server,
            file_path=str(py_file),
            customMethod="test/method",
        )
        # Check what was sent to the server
        method, params = server.requests[0]
        assert method == "test/method"
        assert "textDocument" in params
        assert params["textDocument"]["uri"] == py_file.resolve().as_uri()

    @pytest.mark.asyncio
    async def test_auto_inject_position(self, ops, tmp_path):
        """When line/character provided, position should be auto-injected (0-based)."""
        server = CustomFakeServer(response=None)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._op_customRequest(
            server,
            file_path=str(py_file),
            line=5,
            character=10,
            customMethod="test/method",
        )
        method, params = server.requests[0]
        assert params["position"] == {"line": 4, "character": 9}  # 0-based

    @pytest.mark.asyncio
    async def test_no_inject_when_already_in_params(self, ops, tmp_path):
        """Should NOT override textDocument/position if already in customParams."""
        server = CustomFakeServer(response=None)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        custom_td = {"uri": "file:///custom/path.py"}
        custom_pos = {"line": 99, "character": 88}
        await ops._op_customRequest(
            server,
            file_path=str(py_file),
            line=5,
            character=10,
            customMethod="test/method",
            customParams={
                "textDocument": custom_td,
                "position": custom_pos,
            },
        )
        method, params = server.requests[0]
        # Should keep the caller's values, not auto-inject
        assert params["textDocument"] == custom_td
        assert params["position"] == custom_pos

    @pytest.mark.asyncio
    async def test_string_truncation_in_dict_result(self, ops, tmp_path):
        """Long string values in dict results should be truncated."""
        huge_expansion = "x" * 20000
        server = CustomFakeServer(response={"expansion": huge_expansion})
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        result = await ops._op_customRequest(
            server,
            file_path=str(py_file),
            customMethod="rust-analyzer/expandMacro",
        )
        assert len(result["expansion"]) < 20000
        assert result["_truncated"] is True

    @pytest.mark.asyncio
    async def test_no_file_path(self, ops):
        """Should work without file_path — no textDocument injected."""
        server = CustomFakeServer(response=None)

        result = await ops._op_customRequest(
            server,
            customMethod="test/workspaceMethod",
        )
        # Should succeed (null result)
        assert result["result"] is None
        assert result["_method"] == "test/workspaceMethod"
        # Params should NOT have textDocument
        method, params = server.requests[0]
        assert "textDocument" not in params

    @pytest.mark.asyncio
    async def test_custom_params_forwarded(self, ops, tmp_path):
        """customParams should be forwarded to the server request."""
        server = CustomFakeServer(response=None)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._op_customRequest(
            server,
            file_path=str(py_file),
            customMethod="experimental/ssr",
            customParams={"query": "foo ==>> bar", "parseOnly": True},
        )
        method, params = server.requests[0]
        assert params["query"] == "foo ==>> bar"
        assert params["parseOnly"] is True

    @pytest.mark.asyncio
    async def test_no_auto_inject_when_custom_params_provided(self, ops, tmp_path):
        """When customParams is non-empty, should NOT auto-inject textDocument/position.

        Some custom methods expect flat params (e.g., {uri: "..."}) not nested
        {textDocument: {uri: "..."}}. Auto-injecting mangles the param shape.
        """
        server = CustomFakeServer(response=None)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._op_customRequest(
            server,
            file_path=str(py_file),
            line=5,
            character=10,
            customMethod="rust-analyzer/viewFileText",
            customParams={"uri": py_file.resolve().as_uri()},
        )
        method, params = server.requests[0]
        # Should only contain what the caller passed — no auto-injected keys
        assert "textDocument" not in params
        assert "position" not in params
        assert "uri" in params

    @pytest.mark.asyncio
    async def test_request_exception_raises_not_returns_error(self, ops, tmp_path):
        """Server exceptions should propagate (not be caught as success).

        Bug: _op_customRequest catches exceptions and returns {error: "..."}
        as a normal dict, which tool.py wraps as ToolResult(success=True).
        Fix: re-raise so tool.py catches it and sets success=False.
        """
        server = CustomFakeServer(error=RuntimeError("server crashed"))
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        with pytest.raises(RuntimeError, match="server crashed"):
            await ops._op_customRequest(
                server,
                file_path=str(py_file),
                customMethod="test/failing",
            )
