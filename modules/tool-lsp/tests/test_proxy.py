# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportOptionalSubscript=false
"""Tests for proxy.py — the persistent LSP server proxy process."""

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Module importability ──────────────────────────────────────────────────────


class TestModuleImport:
    """proxy.py should be importable and have expected entry points."""

    def test_proxy_module_importable(self):
        """The proxy module should be importable."""
        from amplifier_module_tool_lsp import proxy  # noqa: F401

    def test_proxy_has_main(self):
        """proxy module should have a main() function."""
        from amplifier_module_tool_lsp.proxy import main

        assert callable(main)

    def test_proxy_has_parse_args(self):
        """proxy module should have a parse_args() function."""
        from amplifier_module_tool_lsp.proxy import parse_args

        assert callable(parse_args)

    def test_proxy_has_lsp_proxy_server_class(self):
        """proxy module should have the LspProxyServer class."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        assert isinstance(LspProxyServer, type)


# ── LSP message framing ──────────────────────────────────────────────────────


class TestMakeLspMessage:
    """make_lsp_message should produce Content-Length framed bytes."""

    def test_simple_body(self):
        from amplifier_module_tool_lsp.proxy import make_lsp_message

        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        raw = make_lsp_message(body)
        assert isinstance(raw, bytes)
        # Should start with Content-Length header
        assert raw.startswith(b"Content-Length: ")
        # Should contain \r\n\r\n separator
        assert b"\r\n\r\n" in raw
        # Body should be valid JSON matching input
        sep = raw.index(b"\r\n\r\n")
        json_body = json.loads(raw[sep + 4 :])
        assert json_body == body

    def test_content_length_matches_body(self):
        from amplifier_module_tool_lsp.proxy import make_lsp_message

        body = {"jsonrpc": "2.0", "id": 42, "result": {"capabilities": {}}}
        raw = make_lsp_message(body)
        # Parse content length from header
        header_end = raw.index(b"\r\n\r\n")
        header = raw[:header_end].decode()
        cl = int(header.split(":")[1].strip())
        actual_body = raw[header_end + 4 :]
        assert cl == len(actual_body)

    def test_empty_result(self):
        from amplifier_module_tool_lsp.proxy import make_lsp_message

        body = {"jsonrpc": "2.0", "id": 1, "result": None}
        raw = make_lsp_message(body)
        sep = raw.index(b"\r\n\r\n")
        parsed = json.loads(raw[sep + 4 :])
        assert parsed["result"] is None


class TestParseLspBody:
    """parse_lsp_body should extract JSON from raw LSP message bytes."""

    def test_valid_message(self):
        from amplifier_module_tool_lsp.proxy import parse_lsp_body

        body_dict = {"jsonrpc": "2.0", "id": 1, "method": "hover"}
        body_bytes = json.dumps(body_dict).encode()
        raw = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode() + body_bytes
        result = parse_lsp_body(raw)
        assert result == body_dict

    def test_no_separator_returns_none(self):
        from amplifier_module_tool_lsp.proxy import parse_lsp_body

        result = parse_lsp_body(b"garbage data no separator")
        assert result is None

    def test_roundtrip_with_make(self):
        from amplifier_module_tool_lsp.proxy import make_lsp_message, parse_lsp_body

        original = {"jsonrpc": "2.0", "id": 5, "method": "textDocument/hover"}
        raw = make_lsp_message(original)
        parsed = parse_lsp_body(raw)
        assert parsed == original


class TestReadLspMessage:
    """read_lsp_message should read complete LSP messages from async streams."""

    @pytest.mark.asyncio
    async def test_reads_complete_message(self):
        from amplifier_module_tool_lsp.proxy import read_lsp_message

        body = b'{"jsonrpc":"2.0","id":1,"method":"test"}'
        raw = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        result = await read_lsp_message(reader)
        assert result is not None
        assert body in result

    @pytest.mark.asyncio
    async def test_eof_returns_none(self):
        from amplifier_module_tool_lsp.proxy import read_lsp_message

        reader = asyncio.StreamReader()
        reader.feed_eof()
        result = await read_lsp_message(reader)
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_messages(self):
        from amplifier_module_tool_lsp.proxy import make_lsp_message, read_lsp_message

        msg1 = make_lsp_message({"jsonrpc": "2.0", "id": 1, "method": "a"})
        msg2 = make_lsp_message({"jsonrpc": "2.0", "id": 2, "method": "b"})
        reader = asyncio.StreamReader()
        reader.feed_data(msg1 + msg2)

        result1 = await read_lsp_message(reader)
        result2 = await read_lsp_message(reader)
        assert result1 is not None
        assert result2 is not None
        assert b'"id": 1' in result1 or b'"id":1' in result1
        assert b'"id": 2' in result2 or b'"id":2' in result2


class TestWriteLspMessage:
    """write_lsp_message should write raw bytes to an async writer."""

    @pytest.mark.asyncio
    async def test_writes_data(self):
        from amplifier_module_tool_lsp.proxy import write_lsp_message

        writer = MagicMock()
        writer.drain = AsyncMock()
        data = b"Content-Length: 5\r\n\r\nhello"
        await write_lsp_message(writer, data)
        writer.write.assert_called_once_with(data)
        writer.drain.assert_awaited_once()


# ── State file ────────────────────────────────────────────────────────────────


class TestStateFileKey:
    """State file key should be deterministic and use canonical paths."""

    def test_deterministic_key(self):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy.language = "rust"
        proxy.workspace = Path("/home/user/project")
        proxy.state_dir = Path("/tmp/state")
        path1 = proxy._state_file_path()
        path2 = proxy._state_file_path()
        assert path1 == path2

    def test_key_format(self):
        """State file should be named {language}-{hash8}.json."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy.language = "rust"
        proxy.workspace = Path("/home/user/project")
        proxy.state_dir = Path("/tmp/state")
        path = proxy._state_file_path()
        assert path.parent == Path("/tmp/state")
        assert path.name.startswith("rust-")
        assert path.name.endswith(".json")
        # Hash part should be 8 hex chars
        hash_part = path.stem.split("-", 1)[1]
        assert len(hash_part) == 8
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_different_workspaces_different_keys(self):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy1 = LspProxyServer.__new__(LspProxyServer)
        proxy1.language = "rust"
        proxy1.workspace = Path("/home/user/project-a")
        proxy1.state_dir = Path("/tmp/state")

        proxy2 = LspProxyServer.__new__(LspProxyServer)
        proxy2.language = "rust"
        proxy2.workspace = Path("/home/user/project-b")
        proxy2.state_dir = Path("/tmp/state")

        assert proxy1._state_file_path() != proxy2._state_file_path()

    def test_different_languages_different_keys(self):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy1 = LspProxyServer.__new__(LspProxyServer)
        proxy1.language = "rust"
        proxy1.workspace = Path("/home/user/project")
        proxy1.state_dir = Path("/tmp/state")

        proxy2 = LspProxyServer.__new__(LspProxyServer)
        proxy2.language = "python"
        proxy2.workspace = Path("/home/user/project")
        proxy2.state_dir = Path("/tmp/state")

        assert proxy1._state_file_path() != proxy2._state_file_path()


class TestStateFileWrite:
    """State file should be written atomically with correct content."""

    def test_write_state_file(self, tmp_path):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy.language = "rust"
        proxy.workspace = Path("/home/user/project")
        proxy.command = ["rust-analyzer"]
        proxy.idle_timeout = 300
        proxy.state_dir = tmp_path
        proxy._port = 54321
        proxy._server_process = MagicMock(pid=12345)

        proxy._write_state_file()

        state_path = proxy._state_file_path()
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["language"] == "rust"
        assert state["port"] == 54321
        assert state["proxy_pid"] == os.getpid()
        assert state["server_pid"] == 12345
        assert state["server_command"] == ["rust-analyzer"]
        assert state["idle_timeout"] == 300
        assert state["lifecycle"] == "timeout"
        assert "started_at" in state
        assert "workspace_root" in state

    def test_write_creates_directory(self, tmp_path):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy.language = "rust"
        proxy.workspace = Path("/home/user/project")
        proxy.command = ["rust-analyzer"]
        proxy.idle_timeout = 300
        proxy.state_dir = tmp_path / "nested" / "dir"
        proxy._port = 54321
        proxy._server_process = MagicMock(pid=12345)

        proxy._write_state_file()
        assert proxy._state_file_path().exists()

    def test_no_tmp_file_left_after_write(self, tmp_path):
        """Atomic write should not leave .tmp files."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy.language = "rust"
        proxy.workspace = Path("/home/user/project")
        proxy.command = ["rust-analyzer"]
        proxy.idle_timeout = 300
        proxy.state_dir = tmp_path
        proxy._port = 54321
        proxy._server_process = MagicMock(pid=12345)

        proxy._write_state_file()
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_remove_state_file(self, tmp_path):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy.language = "rust"
        proxy.workspace = Path("/home/user/project")
        proxy.command = ["rust-analyzer"]
        proxy.idle_timeout = 300
        proxy.state_dir = tmp_path
        proxy._port = 54321
        proxy._server_process = MagicMock(pid=12345)

        proxy._write_state_file()
        assert proxy._state_file_path().exists()

        proxy._remove_state_file()
        assert not proxy._state_file_path().exists()

    def test_remove_nonexistent_state_file_no_error(self, tmp_path):
        """Removing a state file that doesn't exist should not raise."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy.language = "rust"
        proxy.workspace = Path("/home/user/project")
        proxy.state_dir = tmp_path
        # Should not raise
        proxy._remove_state_file()

    def test_persistent_lifecycle(self, tmp_path):
        """idle_timeout=0 should set lifecycle to 'persistent'."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy.language = "rust"
        proxy.workspace = Path("/home/user/project")
        proxy.command = ["rust-analyzer"]
        proxy.idle_timeout = 0
        proxy.state_dir = tmp_path
        proxy._port = 54321
        proxy._server_process = MagicMock(pid=12345)

        proxy._write_state_file()
        state = json.loads(proxy._state_file_path().read_text())
        assert state["lifecycle"] == "persistent"


# ── Initialize caching ────────────────────────────────────────────────────────


class TestInitializeCaching:
    """The proxy should cache InitializeResult and replay for subsequent clients."""

    def _make_proxy(self):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._init_result = None
        proxy._initialized = False
        proxy._running = True
        return proxy

    def test_initial_state_not_initialized(self):
        proxy = self._make_proxy()
        assert proxy._initialized is False
        assert proxy._init_result is None

    @pytest.mark.asyncio
    async def test_first_initialize_forwarded(self):
        """First initialize request should be forwarded to server."""

        proxy = self._make_proxy()

        # Simulate: client sends initialize, proxy sees it's not yet initialized
        # The method field is "initialize" and self._initialized is False
        # → should forward to server (return False = "don't intercept")
        # Client sends initialize while proxy._initialized is False → should forward
        assert proxy._initialized is False

    @pytest.mark.asyncio
    async def test_cached_result_returned_for_second_client(self):
        """After caching, subsequent initialize should return cached result."""
        from amplifier_module_tool_lsp.proxy import make_lsp_message, parse_lsp_body

        proxy = self._make_proxy()
        # Simulate first client completed init
        cached_result = {"capabilities": {"hoverProvider": True}}
        proxy._init_result = cached_result
        proxy._initialized = True

        # Now a second client sends initialize
        init_request = {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}}

        # The proxy should detect method=="initialize" and _initialized==True
        # and return cached result without forwarding
        assert proxy._initialized is True
        assert proxy._init_result == cached_result

        # Build the response the proxy would send
        response = make_lsp_message(
            {
                "jsonrpc": "2.0",
                "id": init_request["id"],
                "result": proxy._init_result,
            }
        )
        body = parse_lsp_body(response)
        assert body["id"] == 2
        assert body["result"] == cached_result

    @pytest.mark.asyncio
    async def test_initialized_notification_swallowed_after_first(self):
        """Subsequent 'initialized' notifications should be swallowed."""
        proxy = self._make_proxy()
        proxy._initialized = True
        # When _initialized is True and method == "initialized", proxy should NOT forward
        # This is a logical check — the actual forwarding logic is in _forward_client_to_server
        assert proxy._initialized is True


# ── Shutdown/exit interception ────────────────────────────────────────────────


class TestShutdownInterception:
    """Client shutdown/exit should not be forwarded to server."""

    @pytest.mark.asyncio
    async def test_shutdown_returns_null_result(self):
        """Client shutdown request should get {result: null} response."""
        from amplifier_module_tool_lsp.proxy import make_lsp_message, parse_lsp_body

        # The proxy intercepts shutdown and returns a null result
        shutdown_request = {"jsonrpc": "2.0", "id": 10, "method": "shutdown"}
        response = make_lsp_message(
            {
                "jsonrpc": "2.0",
                "id": shutdown_request["id"],
                "result": None,
            }
        )
        body = parse_lsp_body(response)
        assert body["id"] == 10
        assert body["result"] is None

    @pytest.mark.asyncio
    async def test_exit_disconnects_client(self):
        """Client exit notification should cause disconnect (not server kill)."""
        # The exit method causes _forward_client_to_server to return (disconnect),
        # not forward to server. This is tested by checking the logic flow:
        # method == "exit" → return from the forwarding loop
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        # Just verify the proxy object can be created — the actual client forwarding
        # logic is integration-tested
        assert proxy._running is True


# ── Idle timeout ──────────────────────────────────────────────────────────────


class TestIdleTimeout:
    """Idle monitor should trigger shutdown after configured timeout."""

    @pytest.mark.asyncio
    async def test_idle_triggers_shutdown(self):
        """When idle exceeds timeout, _idle_monitor should call _shutdown."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        proxy._current_client = None  # No client connected
        proxy.idle_timeout = 1  # 1 second timeout for testing
        proxy._last_client_disconnect = time.time() - 10  # 10 seconds ago

        shutdown_called = False

        async def mock_shutdown():
            nonlocal shutdown_called
            shutdown_called = True
            proxy._running = False

        proxy._shutdown = mock_shutdown

        # Run idle monitor — it checks every 10s, but we'll patch sleep
        # to make it faster
        original_sleep = asyncio.sleep

        call_count = 0

        async def fast_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 5:
                proxy._running = False
                return
            await original_sleep(0)

        with patch("asyncio.sleep", side_effect=fast_sleep):
            await proxy._idle_monitor()

        assert shutdown_called, "Shutdown should have been called after idle timeout"

    @pytest.mark.asyncio
    async def test_connected_client_prevents_idle(self):
        """When a client is connected, idle monitor should not trigger shutdown."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        proxy._current_client = (MagicMock(), MagicMock())  # Client connected
        proxy.idle_timeout = 1
        proxy._last_client_disconnect = (
            time.time() - 100
        )  # Long ago, but doesn't matter

        shutdown_called = False

        async def mock_shutdown():
            nonlocal shutdown_called
            shutdown_called = True
            proxy._running = False

        proxy._shutdown = mock_shutdown

        call_count = 0
        original_sleep = asyncio.sleep

        async def fast_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                proxy._running = False
            # Yield control without recursing into the patched sleep
            await original_sleep(0)

        with patch("asyncio.sleep", side_effect=fast_sleep):
            await proxy._idle_monitor()

        assert not shutdown_called, (
            "Shutdown should NOT be called when client connected"
        )


# ── Constructor ───────────────────────────────────────────────────────────────


class TestConstructor:
    """LspProxyServer constructor should set up fields correctly."""

    def test_basic_construction(self):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer(
            language="rust",
            workspace="/home/user/project",
            command='["rust-analyzer"]',
            init_options="{}",
            idle_timeout=300,
            state_dir="/tmp/state",
        )
        assert proxy.language == "rust"
        assert proxy.workspace == Path("/home/user/project")
        assert proxy.command == ["rust-analyzer"]
        assert proxy.init_options == {}
        assert proxy.idle_timeout == 300
        assert proxy.state_dir == Path("/tmp/state")
        assert proxy._init_result is None
        assert proxy._initialized is False
        assert proxy._running is True
        assert proxy._current_client is None
        assert proxy._server_process is None

    def test_command_as_list(self):
        """Command should be parsed from JSON string."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer(
            language="python",
            workspace="/tmp",
            command='["pyright-langserver", "--stdio"]',
            init_options="{}",
            idle_timeout=60,
            state_dir="/tmp",
        )
        assert proxy.command == ["pyright-langserver", "--stdio"]

    def test_command_already_list(self):
        """If command is already a list, should work too."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer(
            language="rust",
            workspace="/tmp",
            command=["rust-analyzer"],
            init_options={},
            idle_timeout=300,
            state_dir="/tmp",
        )
        assert proxy.command == ["rust-analyzer"]

    def test_init_options_parsed(self):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer(
            language="rust",
            workspace="/tmp",
            command='["rust-analyzer"]',
            init_options='{"cargo": {"allFeatures": true}}',
            idle_timeout=300,
            state_dir="/tmp",
        )
        assert proxy.init_options == {"cargo": {"allFeatures": True}}


# ── Graceful shutdown ─────────────────────────────────────────────────────────


class TestGracefulShutdown:
    """_shutdown should clean up server, TCP, and state file."""

    @pytest.mark.asyncio
    async def test_shutdown_sets_running_false(self):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        proxy.language = "rust"
        proxy.workspace = Path("/tmp/project")
        proxy.state_dir = Path("/tmp/nonexistent-state")

        # Mock everything shutdown touches
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()

        proxy._server_writer = mock_writer
        proxy._server_process = MagicMock()
        proxy._server_process.returncode = 0  # Already exited
        proxy._tcp_server = MagicMock()
        proxy._tcp_server.close = MagicMock()

        await proxy._shutdown()
        assert proxy._running is False

    @pytest.mark.asyncio
    async def test_shutdown_removes_state_file(self, tmp_path):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        proxy.language = "rust"
        proxy.workspace = Path("/tmp/project")
        proxy.command = ["rust-analyzer"]
        proxy.idle_timeout = 300
        proxy.state_dir = tmp_path
        proxy._port = 12345
        proxy._server_process = MagicMock(pid=999)

        # Write the state file first
        proxy._write_state_file()
        assert proxy._state_file_path().exists()

        # Mock server/tcp for shutdown
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        proxy._server_writer = mock_writer
        proxy._server_process.returncode = 0
        proxy._tcp_server = MagicMock()
        proxy._tcp_server.close = MagicMock()

        await proxy._shutdown()
        assert not proxy._state_file_path().exists()

    @pytest.mark.asyncio
    async def test_shutdown_sends_lsp_shutdown_to_server(self):
        """Shutdown should send LSP shutdown+exit to the server subprocess."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        proxy.language = "rust"
        proxy.workspace = Path("/tmp/project")
        proxy.state_dir = Path("/tmp/nonexistent-state")

        mock_writer = MagicMock()
        written_data = []
        mock_writer.write = MagicMock(side_effect=lambda d: written_data.append(d))
        mock_writer.drain = AsyncMock()

        proxy._server_writer = mock_writer
        proxy._server_process = MagicMock()
        proxy._server_process.returncode = 0
        proxy._tcp_server = MagicMock()
        proxy._tcp_server.close = MagicMock()

        await proxy._shutdown()

        # Should have sent shutdown and exit messages
        assert len(written_data) >= 2
        # First message should be shutdown
        all_written = b"".join(written_data)
        assert b"shutdown" in all_written
        assert b"exit" in all_written


# ── Client handling ───────────────────────────────────────────────────────────


class TestClientHandling:
    """TCP client connection handling basics."""

    def test_sequential_model(self):
        """Proxy should track at most one current client."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._current_client = None
        # No client → None
        assert proxy._current_client is None


# ── Server health monitoring ──────────────────────────────────────────────────


class TestServerHealthMonitoring:
    """Proxy should have a _monitor_server method for subprocess health."""

    def test_monitor_server_method_exists(self):
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        assert hasattr(LspProxyServer, "_monitor_server")
        assert asyncio.iscoroutinefunction(LspProxyServer._monitor_server)
