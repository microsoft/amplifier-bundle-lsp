# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportReturnType=false, reportOptionalMemberAccess=false
"""Tests for server.py Task 1 changes.

Note: pyright errors are suppressed because test fakes (FakeProcess, FakeStdin,
FakeStdout) intentionally violate asyncio.subprocess.Process type contracts.
Tests pass at runtime; the fakes are minimal stubs, not full protocol impls.
"""

import asyncio
import json

import pytest

from amplifier_module_tool_lsp.server import LspServer, LspServerManager


# ── Helpers ───────────────────────────────────────────────────────────────────


class FakeProcess:
    """Minimal fake asyncio subprocess for unit tests."""

    def __init__(self):
        self.stdin = FakeStdin()
        self.stdout = FakeStdout()
        self.stderr = None
        self.returncode = None
        self._messages_sent: list[dict] = []

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
        """Add data that will be returned by readline/read."""
        self._buffer += data

    def feed_message(self, message: dict):
        """Feed a complete JSON-RPC message with headers."""
        body = json.dumps(message).encode()
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        self.feed(header + body)

    async def readline(self):
        # Find next newline in buffer
        idx = self._buffer.find(b"\n", self._pos)
        if idx == -1:
            # No more data — block forever (simulates waiting for server output).
            # The test will cancel the reader task to unblock.
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
    server._process = proc
    server._request_id = 0
    server._pending = {}
    server._reader_task = None
    # New fields from Task 1:
    if hasattr(LspServer.__init__, "__code__"):
        # Initialize with timeout if the constructor accepts it
        try:
            server._default_timeout = timeout
        except Exception:
            pass
    try:
        server._diagnostics_cache = {}
    except Exception:
        pass
    return server


# ── Step 1.1: Wire timeout ───────────────────────────────────────────────────


class TestTimeoutWiring:
    """LspServer should accept and use configurable timeout."""

    def test_server_stores_timeout(self):
        """LspServer.__init__ should accept timeout and store as _default_timeout."""
        proc = FakeProcess()
        server = LspServer(
            language="python",
            workspace=None,
            process=proc,
            timeout=45.0,
        )
        assert server._default_timeout == 45.0

    def test_server_default_timeout(self):
        """LspServer should default to 30.0 if no timeout given."""
        proc = FakeProcess()
        server = LspServer(
            language="python",
            workspace=None,
            process=proc,
        )
        assert server._default_timeout == 30.0

    def test_server_manager_stores_timeout(self):
        """LspServerManager should accept and store timeout."""
        mgr = LspServerManager(timeout=60.0)
        assert mgr._timeout == 60.0

    def test_server_manager_default_timeout(self):
        mgr = LspServerManager()
        assert mgr._timeout == 30.0


# ── Step 1.9: Fix asyncio.get_event_loop() ───────────────────────────────────


class TestAsyncioLoop:
    """request() should use get_running_loop(), not get_event_loop()."""

    def test_no_get_event_loop_in_source(self):
        """Source code should not contain asyncio.get_event_loop()."""
        import inspect

        source = inspect.getsource(LspServer)
        assert "get_event_loop()" not in source, (
            "LspServer still uses asyncio.get_event_loop() — "
            "should use asyncio.get_running_loop()"
        )

    def test_uses_get_running_loop(self):
        """Source should use asyncio.get_running_loop()."""
        import inspect

        source = inspect.getsource(LspServer)
        assert "get_running_loop()" in source


# ── Step 1.4: Notification dispatch in _read_responses ───────────────────────


class TestNotificationDispatch:
    """_read_responses should dispatch notifications and server requests."""

    @pytest.mark.asyncio
    async def test_notification_dispatched(self):
        """Notifications (method, no id) should call _handle_notification."""
        server = make_server()
        calls = []

        async def mock_handle_notification(method, params):
            calls.append((method, params))

        server._handle_notification = mock_handle_notification

        # Feed a notification message
        notification = {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": "file:///test.py", "diagnostics": []},
        }
        server._process.stdout.feed_message(notification)

        # Run reader — it processes the message then blocks on next readline.
        # _read_responses catches CancelledError internally and breaks cleanly,
        # so the task finishes normally after cancel.
        task = asyncio.create_task(server._read_responses())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(calls) == 1
        assert calls[0][0] == "textDocument/publishDiagnostics"

    @pytest.mark.asyncio
    async def test_server_request_dispatched(self):
        """Server-initiated requests (method + id) should call _handle_server_request."""
        server = make_server()
        calls = []

        async def mock_handle_server_request(message):
            calls.append(message)

        server._handle_server_request = mock_handle_server_request

        # Feed a server-initiated request (has both method and id)
        request = {
            "jsonrpc": "2.0",
            "id": 999,
            "method": "client/registerCapability",
            "params": {"registrations": []},
        }
        server._process.stdout.feed_message(request)

        task = asyncio.create_task(server._read_responses())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(calls) == 1
        assert calls[0]["method"] == "client/registerCapability"

    @pytest.mark.asyncio
    async def test_response_still_handled(self):
        """Normal responses (id matching pending) should still work."""
        server = make_server()

        future = asyncio.get_running_loop().create_future()
        server._pending[42] = future

        response = {
            "jsonrpc": "2.0",
            "id": 42,
            "result": {"test": "value"},
        }
        server._process.stdout.feed_message(response)

        task = asyncio.create_task(server._read_responses())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert future.done()
        assert future.result() == {"test": "value"}


# ── Step 1.5: Handle server-initiated requests ───────────────────────────────


class TestServerRequestHandling:
    """_handle_server_request should respond appropriately."""

    @pytest.mark.asyncio
    async def test_register_capability_response(self):
        """client/registerCapability should get empty success response."""
        server = make_server()
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "client/registerCapability",
            "params": {"registrations": []},
        }
        await server._handle_server_request(msg)

        # Check that a response was sent
        written = server._process.stdin.written
        assert len(written) == 1
        # Parse the response
        raw = written[0].decode()
        body = raw.split("\r\n\r\n", 1)[1]
        resp = json.loads(body)
        assert resp["id"] == 1
        assert resp["result"] is None
        assert "error" not in resp

    @pytest.mark.asyncio
    async def test_workspace_configuration_response(self):
        """workspace/configuration should return empty config per item."""
        server = make_server()
        msg = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "workspace/configuration",
            "params": {"items": [{"section": "python"}, {"section": "rust"}]},
        }
        await server._handle_server_request(msg)

        written = server._process.stdin.written
        raw = written[0].decode()
        body = raw.split("\r\n\r\n", 1)[1]
        resp = json.loads(body)
        assert resp["id"] == 2
        assert resp["result"] == [{}, {}]

    @pytest.mark.asyncio
    async def test_work_done_progress_create_response(self):
        """window/workDoneProgress/create should get empty success response."""
        server = make_server()
        msg = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "window/workDoneProgress/create",
            "params": {"token": "some-token"},
        }
        await server._handle_server_request(msg)

        written = server._process.stdin.written
        raw = written[0].decode()
        body = raw.split("\r\n\r\n", 1)[1]
        resp = json.loads(body)
        assert resp["id"] == 3
        assert resp["result"] is None

    @pytest.mark.asyncio
    async def test_unknown_request_response(self):
        """Unknown server requests should get null result."""
        server = make_server()
        msg = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "some/unknownMethod",
            "params": {},
        }
        await server._handle_server_request(msg)

        written = server._process.stdin.written
        raw = written[0].decode()
        body = raw.split("\r\n\r\n", 1)[1]
        resp = json.loads(body)
        assert resp["id"] == 4
        assert resp["result"] is None


# ── Step 1.5 helper: _send_response ──────────────────────────────────────────


class TestSendResponse:
    """_send_response should format JSON-RPC responses correctly."""

    @pytest.mark.asyncio
    async def test_send_success_response(self):
        server = make_server()
        await server._send_response(10, result={"data": "value"})

        raw = server._process.stdin.written[0].decode()
        body = raw.split("\r\n\r\n", 1)[1]
        resp = json.loads(body)
        assert resp == {"jsonrpc": "2.0", "id": 10, "result": {"data": "value"}}

    @pytest.mark.asyncio
    async def test_send_error_response(self):
        server = make_server()
        await server._send_response(
            11, error={"code": -32600, "message": "Invalid Request"}
        )

        raw = server._process.stdin.written[0].decode()
        body = raw.split("\r\n\r\n", 1)[1]
        resp = json.loads(body)
        assert resp["id"] == 11
        assert resp["error"] == {"code": -32600, "message": "Invalid Request"}
        assert "result" not in resp

    @pytest.mark.asyncio
    async def test_send_null_result(self):
        server = make_server()
        await server._send_response(12, result=None)

        raw = server._process.stdin.written[0].decode()
        body = raw.split("\r\n\r\n", 1)[1]
        resp = json.loads(body)
        assert resp["id"] == 12
        assert resp["result"] is None


# ── Step 1.6: Diagnostics cache ──────────────────────────────────────────────


class TestDiagnosticsCache:
    """Diagnostics notifications should be cached."""

    def test_diagnostics_cache_exists(self):
        """LspServer should have a _diagnostics_cache dict."""
        proc = FakeProcess()
        server = LspServer(
            language="python",
            workspace=None,
            process=proc,
        )
        assert hasattr(server, "_diagnostics_cache")
        assert isinstance(server._diagnostics_cache, dict)

    @pytest.mark.asyncio
    async def test_publish_diagnostics_cached(self):
        """publishDiagnostics notification should populate cache."""
        server = make_server()
        diagnostics = [{"range": {}, "message": "error", "severity": 1}]
        await server._handle_notification(
            "textDocument/publishDiagnostics",
            {"uri": "file:///test.py", "diagnostics": diagnostics},
        )
        assert server._diagnostics_cache["file:///test.py"] == diagnostics

    @pytest.mark.asyncio
    async def test_diagnostics_cache_overwrites(self):
        """Subsequent diagnostics for same URI should overwrite."""
        server = make_server()
        await server._handle_notification(
            "textDocument/publishDiagnostics",
            {"uri": "file:///test.py", "diagnostics": [{"message": "old"}]},
        )
        await server._handle_notification(
            "textDocument/publishDiagnostics",
            {"uri": "file:///test.py", "diagnostics": [{"message": "new"}]},
        )
        assert server._diagnostics_cache["file:///test.py"] == [{"message": "new"}]

    def test_get_cached_diagnostics(self):
        """get_cached_diagnostics returns cache entry or None."""
        server = make_server()
        server._diagnostics_cache["file:///test.py"] = [{"message": "err"}]
        assert server.get_cached_diagnostics("file:///test.py") == [{"message": "err"}]
        assert server.get_cached_diagnostics("file:///other.py") is None

    @pytest.mark.asyncio
    async def test_other_notifications_ignored(self):
        """Non-diagnostic notifications should not cause errors."""
        server = make_server()
        # Should not raise
        await server._handle_notification(
            "window/logMessage",
            {"type": 3, "message": "some log"},
        )
        assert len(server._diagnostics_cache) == 0


# ── Step 1.8: hierarchicalDocumentSymbolSupport ──────────────────────────────


class TestCapabilities:
    """Client capabilities should include all new features."""

    def _get_capabilities(self) -> dict:
        """Extract capabilities from _initialize method source."""
        # We test by inspecting what _initialize sends. Since we can't easily
        # call it without a real server, we'll check the source code.
        import inspect

        source = inspect.getsource(LspServer._initialize)
        return source

    def test_hierarchical_symbol_support(self):
        source = self._get_capabilities()
        assert "hierarchicalDocumentSymbolSupport" in source

    def test_type_hierarchy_capability(self):
        source = self._get_capabilities()
        assert "typeHierarchy" in source

    def test_rename_capability_with_prepare(self):
        source = self._get_capabilities()
        assert "rename" in source
        assert "prepareSupport" in source

    def test_code_action_capability(self):
        source = self._get_capabilities()
        assert "codeAction" in source
        assert "codeActionLiteralSupport" in source

    def test_inlay_hint_capability(self):
        source = self._get_capabilities()
        assert "inlayHint" in source

    def test_publish_diagnostics_capability(self):
        source = self._get_capabilities()
        assert "publishDiagnostics" in source
        assert "relatedInformation" in source
        assert "tagSupport" in source

    def test_work_done_progress_capability(self):
        source = self._get_capabilities()
        assert "workDoneProgress" in source

    def test_diagnostic_capability(self):
        source = self._get_capabilities()
        assert '"diagnostic"' in source or "'diagnostic'" in source

    def test_experimental_capabilities(self):
        """_initialize should include experimental capabilities for rust-analyzer."""
        source = self._get_capabilities()
        assert '"experimental"' in source or "'experimental'" in source

    def test_experimental_snippet_text_edit(self):
        """experimental block should enable snippetTextEdit."""
        source = self._get_capabilities()
        assert "snippetTextEdit" in source

    def test_experimental_code_action_group(self):
        """experimental block should enable codeActionGroup."""
        source = self._get_capabilities()
        assert "codeActionGroup" in source

    def test_experimental_hover_actions(self):
        """experimental block should enable hoverActions."""
        source = self._get_capabilities()
        assert "hoverActions" in source

    def test_experimental_server_status_notification(self):
        """experimental block should enable serverStatusNotification."""
        source = self._get_capabilities()
        assert "serverStatusNotification" in source

    def test_experimental_commands(self):
        """experimental block should declare supported commands."""
        source = self._get_capabilities()
        assert "rust-analyzer.runSingle" in source
        assert "rust-analyzer.showReferences" in source


# ── Warm-up after create ──────────────────────────────────────────────────────


class TestWarmUp:
    """LspServer.create() should send a warm-up workspace/symbol request after init."""

    @pytest.mark.asyncio
    async def test_warmup_request_sent_after_initialize(self):
        """After _initialize, create() should send workspace/symbol to prime indexing."""
        import inspect

        source = inspect.getsource(LspServer.create)
        # The warm-up should request workspace/symbol with empty query
        assert "workspace/symbol" in source, (
            "LspServer.create() should send a workspace/symbol request after _initialize "
            "to prime the index for reduced cold-start latency"
        )

    @pytest.mark.asyncio
    async def test_warmup_uses_timeout(self):
        """Warm-up request should use asyncio.wait_for with a timeout."""
        import inspect

        source = inspect.getsource(LspServer.create)
        assert "wait_for" in source, (
            "Warm-up request should use asyncio.wait_for to avoid blocking indefinitely"
        )

    @pytest.mark.asyncio
    async def test_warmup_swallows_exceptions(self):
        """Warm-up failure should not prevent server creation."""
        import inspect

        source = inspect.getsource(LspServer.create)
        # Should have exception handling around the warm-up
        assert "except" in source, (
            "Warm-up should catch exceptions so failures don't break server creation"
        )
