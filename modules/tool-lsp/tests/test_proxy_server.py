# pyright: reportAttributeAccessIssue=false, reportReturnType=false, reportArgumentType=false
"""Tests for ProxyLspServer (Task 3) — TCP client connection mode.

ProxyLspServer connects to an LSP proxy via TCP instead of owning a subprocess.
It must have the same public interface as LspServer so operations.py works unchanged.
"""

import asyncio
import json

import pytest


# ── Test 1: Class exists and is importable ──────────────────────────────────


class TestProxyServerClassExists:
    """ProxyLspServer should be importable from server module."""

    def test_proxy_server_class_exists(self):
        from amplifier_module_tool_lsp.server import ProxyLspServer

        assert ProxyLspServer is not None

    def test_proxy_server_is_a_class(self):
        from amplifier_module_tool_lsp.server import ProxyLspServer

        assert isinstance(ProxyLspServer, type)


# ── Test 2: Same interface as LspServer ─────────────────────────────────────


class TestProxyServerInterface:
    """ProxyLspServer must have the same public interface as LspServer."""

    def _make_proxy(self):
        """Create a ProxyLspServer with fake reader/writer, bypassing connect()."""
        from pathlib import Path

        from amplifier_module_tool_lsp.server import ProxyLspServer

        server = ProxyLspServer.__new__(ProxyLspServer)
        server.language = "python"
        server.workspace = Path("/tmp/test")
        server._reader = None
        server._writer = None
        server._port = 9999
        server._request_id = 0
        server._pending = {}
        server._reader_task = None
        server._default_timeout = 30.0
        server._diagnostics_cache = {}
        return server

    def test_has_language_attribute(self):
        server = self._make_proxy()
        assert hasattr(server, "language")
        assert server.language == "python"

    def test_has_workspace_attribute(self):
        server = self._make_proxy()
        assert hasattr(server, "workspace")

    def test_has_diagnostics_cache(self):
        server = self._make_proxy()
        assert hasattr(server, "_diagnostics_cache")
        assert isinstance(server._diagnostics_cache, dict)

    def test_has_request_method(self):
        server = self._make_proxy()
        assert callable(getattr(server, "request", None))

    def test_has_notify_method(self):
        server = self._make_proxy()
        assert callable(getattr(server, "notify", None))

    def test_has_get_cached_diagnostics_method(self):
        server = self._make_proxy()
        assert callable(getattr(server, "get_cached_diagnostics", None))

    def test_has_shutdown_method(self):
        server = self._make_proxy()
        assert callable(getattr(server, "shutdown", None))

    def test_has_connect_classmethod(self):
        from amplifier_module_tool_lsp.server import ProxyLspServer

        assert callable(getattr(ProxyLspServer, "connect", None))


# ── Test 3: connect() factory ───────────────────────────────────────────────


class FakeStreamWriter:
    """Minimal fake asyncio.StreamWriter for tests."""

    def __init__(self):
        self.written: list[bytes] = []
        self._closed = False

    def write(self, data: bytes):
        self.written.append(data)

    async def drain(self):
        pass

    def close(self):
        self._closed = True

    async def wait_closed(self):
        pass


class FakeStreamReader:
    """Minimal fake asyncio.StreamReader for tests."""

    def __init__(self):
        self._buffer = b""
        self._pos = 0
        self._wait_event = asyncio.Event()

    def feed(self, data: bytes):
        self._buffer += data

    def feed_message(self, message: dict):
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


class TestProxyServerConnect:
    """connect() should open TCP, start reader, do init handshake."""

    @pytest.mark.asyncio
    async def test_connect_opens_tcp_and_initializes(self):
        """connect() should call open_connection, start reader task, and initialize."""
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from amplifier_module_tool_lsp.server import ProxyLspServer

        fake_reader = FakeStreamReader()
        fake_writer = FakeStreamWriter()

        # Feed the initialize response and handle initialized notification
        init_response = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
        fake_reader.feed_message(init_response)

        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_conn:
            mock_conn.return_value = (fake_reader, fake_writer)

            server = await ProxyLspServer.connect(
                language="python",
                workspace=Path("/tmp/test"),
                port=12345,
                init_options={"setting": "value"},
            )

        # Verify it connected to the right address
        mock_conn.assert_called_once_with("127.0.0.1", 12345)

        # Verify attributes are set
        assert server.language == "python"
        assert server.workspace == Path("/tmp/test")
        assert server._port == 12345

        # Verify reader task was started
        assert server._reader_task is not None

        # Verify initialize request was sent (first thing written)
        assert len(fake_writer.written) >= 1
        first_msg_raw = fake_writer.written[0].decode()
        body = first_msg_raw.split("\r\n\r\n", 1)[1]
        first_msg = json.loads(body)
        assert first_msg["method"] == "initialize"

        # Clean up reader task
        server._reader_task.cancel()
        try:
            await server._reader_task
        except asyncio.CancelledError:
            pass


# ── Test 4: request() sends over TCP ────────────────────────────────────────


class TestProxyServerRequest:
    """request() should send Content-Length framed JSON-RPC over TCP."""

    def _make_proxy_with_fakes(self):
        from pathlib import Path

        from amplifier_module_tool_lsp.server import ProxyLspServer

        reader = FakeStreamReader()
        writer = FakeStreamWriter()
        server = ProxyLspServer.__new__(ProxyLspServer)
        server.language = "python"
        server.workspace = Path("/tmp/test")
        server._reader = reader
        server._writer = writer
        server._port = 9999
        server._request_id = 0
        server._pending = {}
        server._reader_task = None
        server._default_timeout = 30.0
        server._diagnostics_cache = {}
        return server, reader, writer

    @pytest.mark.asyncio
    async def test_request_sends_content_length_framing(self):
        """request() should write Content-Length header + JSON body to writer."""
        server, reader, writer = self._make_proxy_with_fakes()

        # Start reader task to process responses
        reader.feed_message({"jsonrpc": "2.0", "id": 1, "result": {"test": "ok"}})
        server._reader_task = asyncio.create_task(server._read_responses())

        result = await server.request("textDocument/hover", {"position": {"line": 1}})

        assert result == {"test": "ok"}

        # Verify Content-Length framing was written
        raw = writer.written[0].decode()
        assert raw.startswith("Content-Length: ")
        assert "\r\n\r\n" in raw

        # Verify JSON body
        body = raw.split("\r\n\r\n", 1)[1]
        msg = json.loads(body)
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == "textDocument/hover"
        assert msg["id"] == 1

        server._reader_task.cancel()
        try:
            await server._reader_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_notify_sends_without_id(self):
        """notify() should send a message without id field."""
        server, _reader, writer = self._make_proxy_with_fakes()

        await server.notify("initialized", {})

        raw = writer.written[0].decode()
        body = raw.split("\r\n\r\n", 1)[1]
        msg = json.loads(body)
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == "initialized"
        assert "id" not in msg


# ── Test 5: shutdown() does not kill process ────────────────────────────────


class TestProxyServerShutdown:
    """shutdown() should disconnect TCP, NOT kill any process."""

    @pytest.mark.asyncio
    async def test_shutdown_closes_tcp_connection(self):
        """shutdown() should close the writer (TCP connection)."""
        from pathlib import Path

        from amplifier_module_tool_lsp.server import ProxyLspServer

        reader = FakeStreamReader()
        writer = FakeStreamWriter()

        server = ProxyLspServer.__new__(ProxyLspServer)
        server.language = "python"
        server.workspace = Path("/tmp/test")
        server._reader = reader
        server._writer = writer
        server._port = 9999
        server._request_id = 0
        server._pending = {}
        server._reader_task = None
        server._default_timeout = 5.0
        server._diagnostics_cache = {}

        await server.shutdown()

        assert writer._closed is True

    @pytest.mark.asyncio
    async def test_shutdown_has_no_process_kill(self):
        """ProxyLspServer should not have _process attribute or call .kill()."""
        from amplifier_module_tool_lsp.server import ProxyLspServer

        # Verify no _process attribute or .kill() call in shutdown
        import inspect

        source = inspect.getsource(ProxyLspServer.shutdown)
        assert ".kill()" not in source, "shutdown() should not call .kill()"
        assert "_process" not in source, "shutdown() should not reference _process"

    @pytest.mark.asyncio
    async def test_shutdown_cancels_reader_task(self):
        """shutdown() should cancel the background reader task."""
        from pathlib import Path

        from amplifier_module_tool_lsp.server import ProxyLspServer

        reader = FakeStreamReader()
        writer = FakeStreamWriter()

        server = ProxyLspServer.__new__(ProxyLspServer)
        server.language = "python"
        server.workspace = Path("/tmp/test")
        server._reader = reader
        server._writer = writer
        server._port = 9999
        server._request_id = 0
        server._pending = {}
        server._default_timeout = 5.0
        server._diagnostics_cache = {}

        # Create a reader task that will block
        server._reader_task = asyncio.create_task(server._read_responses())
        await asyncio.sleep(0.01)

        await server.shutdown()

        assert server._reader_task.done()


# ── Test 6: CLIENT_CAPABILITIES constant ────────────────────────────────────


class TestClientCapabilitiesConstant:
    """CLIENT_CAPABILITIES should be a module-level constant."""

    def test_constant_exists(self):
        from amplifier_module_tool_lsp.server import CLIENT_CAPABILITIES

        assert isinstance(CLIENT_CAPABILITIES, dict)

    def test_has_text_document_key(self):
        from amplifier_module_tool_lsp.server import CLIENT_CAPABILITIES

        assert "textDocument" in CLIENT_CAPABILITIES

    def test_has_workspace_key(self):
        from amplifier_module_tool_lsp.server import CLIENT_CAPABILITIES

        assert "workspace" in CLIENT_CAPABILITIES

    def test_has_experimental_key(self):
        from amplifier_module_tool_lsp.server import CLIENT_CAPABILITIES

        assert "experimental" in CLIENT_CAPABILITIES

    def test_has_definition_capability(self):
        from amplifier_module_tool_lsp.server import CLIENT_CAPABILITIES

        assert "definition" in CLIENT_CAPABILITIES["textDocument"]

    def test_has_hover_capability(self):
        from amplifier_module_tool_lsp.server import CLIENT_CAPABILITIES

        assert "hover" in CLIENT_CAPABILITIES["textDocument"]

    def test_has_rename_with_prepare(self):
        from amplifier_module_tool_lsp.server import CLIENT_CAPABILITIES

        rename = CLIENT_CAPABILITIES["textDocument"]["rename"]
        assert rename["prepareSupport"] is True

    def test_has_publish_diagnostics(self):
        from amplifier_module_tool_lsp.server import CLIENT_CAPABILITIES

        assert "publishDiagnostics" in CLIENT_CAPABILITIES["textDocument"]


# ── Test 7: LspServer still uses extracted constant ─────────────────────────


class TestLspServerUsesClientCapabilities:
    """After extracting CLIENT_CAPABILITIES, LspServer._initialize should still work."""

    def test_lsp_server_initialize_references_constant(self):
        """LspServer._initialize should reference CLIENT_CAPABILITIES."""
        import inspect

        from amplifier_module_tool_lsp.server import LspServer

        source = inspect.getsource(LspServer._initialize)
        assert "CLIENT_CAPABILITIES" in source, (
            "LspServer._initialize should use the CLIENT_CAPABILITIES constant"
        )

    def test_capabilities_content_unchanged(self):
        """CLIENT_CAPABILITIES should have all the capabilities LspServer had."""
        from amplifier_module_tool_lsp.server import CLIENT_CAPABILITIES

        td = CLIENT_CAPABILITIES["textDocument"]
        # All capabilities that were in the original LspServer._initialize
        assert "definition" in td
        assert "references" in td
        assert "hover" in td
        assert "documentSymbol" in td
        assert "implementation" in td
        assert "callHierarchy" in td
        assert "typeHierarchy" in td
        assert "diagnostic" in td
        assert "rename" in td
        assert "codeAction" in td
        assert "inlayHint" in td
        assert "publishDiagnostics" in td

        ws = CLIENT_CAPABILITIES["workspace"]
        assert "symbol" in ws
        assert "workDoneProgress" in ws

        exp = CLIENT_CAPABILITIES["experimental"]
        assert "snippetTextEdit" in exp
        assert "codeActionGroup" in exp
        assert "hoverActions" in exp
        assert "serverStatusNotification" in exp


# ── Test 8: Diagnostics cache ──────────────────────────────────────────────


class TestProxyDiagnosticsCache:
    """ProxyLspServer should cache diagnostics from notifications."""

    def _make_proxy(self):
        from pathlib import Path

        from amplifier_module_tool_lsp.server import ProxyLspServer

        server = ProxyLspServer.__new__(ProxyLspServer)
        server.language = "python"
        server.workspace = Path("/tmp/test")
        server._reader = None
        server._writer = FakeStreamWriter()
        server._port = 9999
        server._request_id = 0
        server._pending = {}
        server._reader_task = None
        server._default_timeout = 30.0
        server._diagnostics_cache = {}
        return server

    def test_get_cached_diagnostics_returns_none_for_unknown(self):
        server = self._make_proxy()
        assert server.get_cached_diagnostics("file:///unknown.py") is None

    @pytest.mark.asyncio
    async def test_handle_notification_caches_diagnostics(self):
        server = self._make_proxy()
        diagnostics = [{"range": {}, "message": "error", "severity": 1}]
        await server._handle_notification(
            "textDocument/publishDiagnostics",
            {"uri": "file:///test.py", "diagnostics": diagnostics},
        )
        assert server.get_cached_diagnostics("file:///test.py") == diagnostics

    @pytest.mark.asyncio
    async def test_handle_notification_ignores_other_methods(self):
        server = self._make_proxy()
        await server._handle_notification(
            "window/logMessage",
            {"type": 3, "message": "some log"},
        )
        assert len(server._diagnostics_cache) == 0

    @pytest.mark.asyncio
    async def test_diagnostics_cache_overwrites(self):
        server = self._make_proxy()
        await server._handle_notification(
            "textDocument/publishDiagnostics",
            {"uri": "file:///test.py", "diagnostics": [{"message": "old"}]},
        )
        await server._handle_notification(
            "textDocument/publishDiagnostics",
            {"uri": "file:///test.py", "diagnostics": [{"message": "new"}]},
        )
        assert server.get_cached_diagnostics("file:///test.py") == [{"message": "new"}]
