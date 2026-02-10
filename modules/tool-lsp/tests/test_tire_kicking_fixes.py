"""Tests for tire-kicking fixes: workspaceSymbol file_path, error messages, cold-start race."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from amplifier_module_tool_lsp.operations import LspOperations
from amplifier_module_tool_lsp.server import LspServer
from amplifier_module_tool_lsp.tool import LspTool


# ── Helpers ──────────────────────────────────────────────────────────────────


class FakeServer:
    """Fake LspServer that records notifications sent to it."""

    def __init__(self):
        self.language = "python"
        self.notifications: list[tuple[str, dict]] = []

    async def notify(self, method: str, params: dict):
        self.notifications.append((method, params))

    async def request(self, method: str, params: dict):
        return None


class ScriptedFakeServer:
    """Fake LspServer that returns scripted responses per-method."""

    def __init__(self, responses=None, errors=None):
        self.language = "python"
        self.notifications: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, dict]] = []
        self._responses = responses or {}
        self._errors = errors or {}

    async def notify(self, method: str, params: dict):
        self.notifications.append((method, params))

    async def request(self, method: str, params: dict):
        self.requests.append((method, params))
        if method in self._errors:
            raise self._errors[method]
        return self._responses.get(method)


class FakeProcess:
    """Minimal fake asyncio subprocess for unit tests."""

    def __init__(self):
        self.stdin = FakeStdin()
        self.stdout = FakeStdout()
        self.stderr = None
        self.returncode = None

    async def communicate(self, input=None):
        return b"", b""

    async def wait(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9


class FakeStdin:
    def __init__(self):
        self.written: list[bytes] = []
        self._closed = False

    def write(self, data: bytes):
        self.written.append(data)

    async def drain(self):
        pass

    def close(self):
        self._closed = True


class FakeStdout:
    def __init__(self):
        self._buffer = b""
        self._pos = 0
        self._wait_event = asyncio.Event()

    def feed(self, data: bytes):
        self._buffer += data

    def feed_message(self, message: dict):
        import json

        body = json.dumps(message).encode()
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        self.feed(header + body)

    async def readline(self):
        idx = self._buffer.find(b"\n", self._pos)
        if idx == -1:
            await self._wait_event.wait()
            return b""
        line = self._buffer[self._pos : idx + 1]
        self._pos = idx + 1
        return line

    async def read(self, n: int):
        data = self._buffer[self._pos : self._pos + n]
        self._pos += n
        return data


def make_server(timeout: float = 30.0) -> LspServer:
    """Create an LspServer with a fake process, bypassing actual init."""
    proc = FakeProcess()
    server = LspServer.__new__(LspServer)
    server.language = "python"
    server.workspace = None
    server._process = proc  # type: ignore[assignment]
    server._request_id = 0
    server._pending = {}
    server._reader_task = None
    server._default_timeout = timeout
    server._diagnostics_cache = {}
    return server


@pytest.fixture
def tool():
    """Create an LspTool with minimal config."""
    return LspTool(
        {
            "languages": {
                "python": {
                    "extensions": [".py"],
                    "workspace_markers": ["pyproject.toml"],
                    "server": {"command": ["pyright-langserver", "--stdio"]},
                }
            },
            "timeout_seconds": 30,
        }
    )


# ── Fix 1: workspaceSymbol without file_path ─────────────────────────────────


class TestWorkspaceSymbolWithoutFilePath:
    """workspaceSymbol should work without file_path, like customRequest."""

    @pytest.mark.asyncio
    async def test_workspace_symbol_not_rejected_without_file_path(self, tool):
        """workspaceSymbol without file_path should NOT get 'file_path is required' error."""
        result = await tool.execute(
            {
                "operation": "workspaceSymbol",
                "query": "MyClass",
            }
        )
        # Should NOT fail with "file_path is required" error.
        # It may fail trying to start server, but that's a different error.
        if not result.success and result.error:
            assert "file_path is required" not in result.error.get("message", "")

    @pytest.mark.asyncio
    async def test_workspace_symbol_no_languages_returns_error(self):
        """workspaceSymbol without file_path and no languages configured → error."""
        empty_tool = LspTool({"languages": {}})
        result = await empty_tool.execute(
            {
                "operation": "workspaceSymbol",
                "query": "MyClass",
            }
        )
        assert result.success is False
        combined = (str(result.output) + str(result.error)).lower()
        assert "no languages" in combined

    @pytest.mark.asyncio
    async def test_workspace_symbol_uses_first_language(self, tool):
        """workspaceSymbol without file_path should use first configured language."""
        result = await tool.execute(
            {
                "operation": "workspaceSymbol",
                "query": "MyClass",
            }
        )
        # Should NOT fail with language detection or file_path errors
        if not result.success and result.error:
            msg = result.error.get("message", "")
            assert "file_path is required" not in msg
            assert "No LSP support configured" not in msg

    @pytest.mark.asyncio
    async def test_operations_execute_workspace_symbol_without_file_path(self):
        """LspOperations.execute should handle workspaceSymbol without file_path (no _open_document)."""
        ops = LspOperations(server_manager=None)
        server = ScriptedFakeServer(
            responses={
                "workspace/symbol": [{"name": "MyClass", "kind": 5}],
            }
        )

        # Should not raise AssertionError about file_path being None
        result = await ops.execute(
            server=server,
            operation="workspaceSymbol",
            file_path=None,
            line=1,
            character=1,
            query="MyClass",
        )
        assert result is not None
        # Should NOT have sent any didOpen notification
        assert len(server.notifications) == 0

    @pytest.mark.asyncio
    async def test_operations_execute_workspace_symbol_with_file_path_still_works(
        self, tmp_path
    ):
        """workspaceSymbol with file_path should still work (backward compat)."""
        ops = LspOperations(server_manager=None)
        server = ScriptedFakeServer(
            responses={
                "workspace/symbol": [{"name": "MyClass", "kind": 5}],
            }
        )
        py_file = tmp_path / "test.py"
        py_file.write_text("class MyClass: pass\n")

        result = await ops.execute(
            server=server,
            operation="workspaceSymbol",
            file_path=str(py_file),
            line=1,
            character=1,
            query="MyClass",
        )
        assert result is not None


# ── Fix 2: Empty error messages ──────────────────────────────────────────────


class TestEmptyErrorMessages:
    """LSP errors with empty message should show error code instead of blank."""

    @pytest.mark.asyncio
    async def test_empty_error_message_shows_code(self):
        """Error with empty message string should show error code."""
        server = make_server()

        future = asyncio.get_running_loop().create_future()
        server._pending[1] = future

        # Feed an error response with empty message
        error_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": ""},
        }
        server._process.stdout.feed_message(error_response)

        task = asyncio.create_task(server._read_responses())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert future.done()
        exc = future.exception()
        assert exc is not None
        # Should NOT be empty — should mention the error code
        assert str(exc) != ""
        assert "-32600" in str(exc) or "LSP error" in str(exc)

    @pytest.mark.asyncio
    async def test_method_not_found_error_message(self):
        """Error code -32601 should show 'Method not supported by server'."""
        server = make_server()

        future = asyncio.get_running_loop().create_future()
        server._pending[2] = future

        # Feed a -32601 Method Not Found error
        error_response = {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32601, "message": "Method not found"},
        }
        server._process.stdout.feed_message(error_response)

        task = asyncio.create_task(server._read_responses())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert future.done()
        exc = future.exception()
        assert exc is not None
        assert "not supported" in str(exc).lower() or "method not found" in str(exc).lower()

    @pytest.mark.asyncio
    async def test_normal_error_message_preserved(self):
        """Error with a normal message should still work as before."""
        server = make_server()

        future = asyncio.get_running_loop().create_future()
        server._pending[3] = future

        error_response = {
            "jsonrpc": "2.0",
            "id": 3,
            "error": {"code": -32700, "message": "Parse error"},
        }
        server._process.stdout.feed_message(error_response)

        task = asyncio.create_task(server._read_responses())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert future.done()
        exc = future.exception()
        assert exc is not None
        assert "Parse error" in str(exc)


class TestToolErrorMessageFallback:
    """Tool-level catch-all should never produce blank error messages."""

    @pytest.mark.asyncio
    async def test_empty_exception_message_shows_type(self):
        """Exception with empty str should show type name."""
        tool = LspTool(
            {
                "languages": {
                    "python": {
                        "extensions": [".py"],
                        "workspace_markers": ["pyproject.toml"],
                        "server": {"command": ["pyright-langserver", "--stdio"]},
                    }
                },
            }
        )

        # Mock the operations.execute to raise an exception with empty message
        with patch.object(
            tool._operations, "execute", side_effect=TimeoutError()
        ):
            # Also mock get_server to return a fake server
            with patch.object(
                tool._server_manager,
                "get_server",
                new_callable=AsyncMock,
                return_value=FakeServer(),
            ):
                result = await tool.execute(
                    {
                        "operation": "documentSymbol",
                        "file_path": "/tmp/test.py",
                    }
                )

        assert result.success is False
        msg = result.error["message"]
        # Should NOT be "LSP operation failed: " (blank after colon)
        assert msg != "LSP operation failed: "
        assert "TimeoutError" in msg or "timeout" in msg.lower()


# ── Fix 3: Cold-start race condition ─────────────────────────────────────────


class TestColdStartDelay:
    """First didOpen should have a brief delay for server processing."""

    @pytest.mark.asyncio
    async def test_first_open_has_delay(self, tmp_path):
        """First _open_document call should include a small delay after didOpen."""
        ops = LspOperations(server_manager=None)
        server = FakeServer()
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        with patch("amplifier_module_tool_lsp.operations.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await ops._open_document(server, str(py_file))

        # Should have called sleep(0.1) after first didOpen
        mock_sleep.assert_called_once_with(0.1)

    @pytest.mark.asyncio
    async def test_second_open_unchanged_no_delay(self, tmp_path):
        """Second call with unchanged file should NOT have delay (no notification sent)."""
        ops = LspOperations(server_manager=None)
        server = FakeServer()
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        # First open — will have delay
        await ops._open_document(server, str(py_file))

        # Second open — unchanged, no notification, no delay
        with patch("amplifier_module_tool_lsp.operations.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await ops._open_document(server, str(py_file))

        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_second_open_changed_no_delay(self, tmp_path):
        """didChange (second open with changed content) should NOT have delay."""
        ops = LspOperations(server_manager=None)
        server = FakeServer()
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._open_document(server, str(py_file))

        py_file.write_text("x = 2\n")

        with patch("amplifier_module_tool_lsp.operations.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await ops._open_document(server, str(py_file))

        # didChange should NOT trigger delay
        mock_sleep.assert_not_called()


class TestFileNotFoundRetry:
    """'file not found' errors should retry once after a delay."""

    @pytest.mark.asyncio
    async def test_retry_on_file_not_found(self, tmp_path):
        """Execute should retry once when operation raises 'file not found'."""
        ops = LspOperations(server_manager=None)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        call_count = 0

        async def flaky_request(method, params):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and method == "textDocument/definition":
                raise Exception("file not found in project")
            return {"uri": "file:///test.py", "range": {"start": {"line": 0, "character": 0}}}

        server = ScriptedFakeServer()
        server.request = flaky_request  # type: ignore[assignment]

        with patch("amplifier_module_tool_lsp.operations.asyncio.sleep", new_callable=AsyncMock):
            result = await ops.execute(
                server=server,
                operation="goToDefinition",
                file_path=str(py_file),
                line=1,
                character=1,
                query=None,
            )

        # Should have retried and succeeded
        assert result is not None
        # request was called twice (first failed, retry succeeded)
        # +1 for the didOpen notification's implicit calls
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_other_errors(self, tmp_path):
        """Non 'file not found' errors should NOT be retried."""
        ops = LspOperations(server_manager=None)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        server = ScriptedFakeServer(
            errors={
                "textDocument/definition": Exception("some random error"),
            }
        )

        with pytest.raises(Exception, match="some random error"):
            await ops.execute(
                server=server,
                operation="goToDefinition",
                file_path=str(py_file),
                line=1,
                character=1,
                query=None,
            )

    @pytest.mark.asyncio
    async def test_retry_uses_delay(self, tmp_path):
        """Retry should wait 0.5s before second attempt."""
        ops = LspOperations(server_manager=None)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        call_count = 0

        async def flaky_request(method, params):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and method == "textDocument/definition":
                raise Exception("file not found")
            return None

        server = ScriptedFakeServer()
        server.request = flaky_request  # type: ignore[assignment]

        with patch("amplifier_module_tool_lsp.operations.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await ops.execute(
                server=server,
                operation="goToDefinition",
                file_path=str(py_file),
                line=1,
                character=1,
                query=None,
            )

        # Should have called sleep(0.5) for the retry delay
        # (also sleep(0.1) for the initial didOpen)
        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert 0.5 in sleep_args
