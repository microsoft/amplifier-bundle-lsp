# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportOptionalSubscript=false
"""Tests for queue-based server stdout reading, auto-restart, and pipe deadlock prevention.

Fix 1: Persistent _read_server_stdout prevents pipe backpressure
Fix 2: Queue-based _forward_server_to_client prevents stream corruption
Fix 3: Auto-restart in _monitor_server prevents proxy death on RA crash
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_lsp.proxy import (
    LspProxyServer,
    make_lsp_message,
    parse_lsp_body,
)


# -- Helpers -------------------------------------------------------------------


def _make_proxy():
    """Create a proxy with queue-based attributes for testing."""
    proxy = LspProxyServer.__new__(LspProxyServer)
    proxy._init_result = None
    proxy._initialized = False
    proxy._pending_init_id = None
    proxy._running = True
    proxy._current_client = None
    proxy._server_messages = asyncio.Queue(maxsize=200)
    proxy._server_reader_task = None
    proxy._server_process = None
    proxy._server_reader = None
    proxy._server_writer = None
    proxy._tcp_server = None
    proxy._port = 0
    proxy._last_client_disconnect = time.time()
    proxy.language = "rust"
    proxy.workspace = Path("/tmp/test-ws")
    proxy.command = ["rust-analyzer"]
    proxy.idle_timeout = 300
    proxy.state_dir = Path("/tmp/state")
    return proxy


# -- Fix 1: _read_server_stdout populates queue --------------------------------


class TestReadServerStdout:
    """_read_server_stdout reads LSP messages from server stdout into a queue."""

    @pytest.mark.asyncio
    async def test_server_stdout_read_into_queue(self):
        """Messages from server stdout should appear in _server_messages queue."""
        proxy = _make_proxy()

        reader = asyncio.StreamReader()
        msg1 = make_lsp_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {},
            }
        )
        msg2 = make_lsp_message({"jsonrpc": "2.0", "id": 1, "result": {"test": True}})
        reader.feed_data(msg1 + msg2)
        reader.feed_eof()
        proxy._server_reader = reader

        await proxy._read_server_stdout()

        raw1 = await proxy._server_messages.get()
        assert raw1 is not None
        body1 = parse_lsp_body(raw1)
        assert body1["method"] == "textDocument/publishDiagnostics"

        raw2 = await proxy._server_messages.get()
        assert raw2 is not None
        body2 = parse_lsp_body(raw2)
        assert body2["id"] == 1

    @pytest.mark.asyncio
    async def test_caches_init_response_in_reader(self):
        """_read_server_stdout should cache InitializeResult by ID correlation."""
        proxy = _make_proxy()
        proxy._pending_init_id = 42

        init_response = make_lsp_message(
            {
                "jsonrpc": "2.0",
                "id": 42,
                "result": {"capabilities": {"hoverProvider": True}},
            }
        )
        reader = asyncio.StreamReader()
        reader.feed_data(init_response)
        reader.feed_eof()
        proxy._server_reader = reader

        await proxy._read_server_stdout()

        assert proxy._initialized is True
        assert proxy._init_result == {"capabilities": {"hoverProvider": True}}
        assert proxy._pending_init_id is None

    @pytest.mark.asyncio
    async def test_eof_puts_sentinel(self):
        """EOF from server should put None sentinel in queue."""
        proxy = _make_proxy()

        reader = asyncio.StreamReader()
        reader.feed_eof()
        proxy._server_reader = reader

        await proxy._read_server_stdout()

        sentinel = await proxy._server_messages.get()
        assert sentinel is None

    @pytest.mark.asyncio
    async def test_queue_full_drops_oldest(self):
        """When queue hits maxsize, oldest message should be discarded."""
        proxy = _make_proxy()
        proxy._server_messages = asyncio.Queue(maxsize=2)

        reader = asyncio.StreamReader()
        msg1 = make_lsp_message({"jsonrpc": "2.0", "id": 1, "result": "first"})
        msg2 = make_lsp_message({"jsonrpc": "2.0", "id": 2, "result": "second"})
        msg3 = make_lsp_message({"jsonrpc": "2.0", "id": 3, "result": "third"})
        reader.feed_data(msg1 + msg2 + msg3)
        reader.feed_eof()
        proxy._server_reader = reader

        await proxy._read_server_stdout()

        # Queue was maxsize=2; 3 messages fed. On msg3 the queue is full,
        # so msg1 (oldest) gets dropped. Queue ends up with [msg2, msg3].
        # The EOF sentinel tries put_nowait but queue is full — silently dropped.
        items = []
        while not proxy._server_messages.empty():
            items.append(proxy._server_messages.get_nowait())

        assert len(items) == 2
        # msg1 should have been dropped — only msg2 and msg3 remain
        body_first = parse_lsp_body(items[0])
        body_second = parse_lsp_body(items[1])
        assert body_first["id"] == 2, "Oldest message (id=1) should have been dropped"
        assert body_second["id"] == 3


# -- Fix 2: _forward_server_to_client uses queue ------------------------------


class TestForwardServerToClientQueue:
    """_forward_server_to_client reads from _server_messages queue, not stream."""

    @pytest.mark.asyncio
    async def test_forward_uses_queue_not_stream(self):
        """Messages from queue should be forwarded to client writer."""
        proxy = _make_proxy()

        msg = make_lsp_message({"jsonrpc": "2.0", "id": 1, "result": {"hover": "info"}})
        await proxy._server_messages.put(msg)
        await proxy._server_messages.put(None)  # EOF sentinel

        written = []
        client_writer = MagicMock()
        client_writer.write = MagicMock(side_effect=lambda d: written.append(d))
        client_writer.drain = AsyncMock()

        await proxy._forward_server_to_client(client_writer)

        assert len(written) == 1
        body = parse_lsp_body(written[0])
        assert body["id"] == 1
        assert body["result"] == {"hover": "info"}

    @pytest.mark.asyncio
    async def test_client_done_stops_forwarding(self):
        """When client_done is set, forwarding should stop promptly."""
        proxy = _make_proxy()

        client_done = asyncio.Event()
        client_done.set()  # Already signaled

        client_writer = MagicMock()
        client_writer.write = MagicMock()
        client_writer.drain = AsyncMock()

        # Put a message that should NOT be forwarded
        msg = make_lsp_message({"jsonrpc": "2.0", "id": 1, "result": "data"})
        await proxy._server_messages.put(msg)

        await proxy._forward_server_to_client(client_writer, client_done)

        client_writer.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_eof_sentinel_stops_forwarding(self):
        """None sentinel in queue should cause forwarding to return."""
        proxy = _make_proxy()

        await proxy._server_messages.put(None)

        client_writer = MagicMock()
        client_writer.write = MagicMock()
        client_writer.drain = AsyncMock()

        await proxy._forward_server_to_client(client_writer)

        client_writer.write.assert_not_called()


# -- Fix 3: _monitor_server with auto-restart ---------------------------------


class TestMonitorServerRestart:
    """_monitor_server should restart the server on crash instead of shutting down."""

    @pytest.mark.asyncio
    async def test_server_restart_on_crash(self):
        """Server crash should trigger restart, resetting init state."""
        proxy = _make_proxy()
        start_server_called = 0

        async def mock_start_server():
            nonlocal start_server_called
            start_server_called += 1
            # Create a new process that stays alive (blocks forever)
            new_process = MagicMock()

            async def long_wait():
                await asyncio.sleep(100)

            new_process.wait = long_wait
            proxy._server_process = new_process
            proxy._server_reader = asyncio.StreamReader()
            proxy._server_reader.feed_eof()

        # First process crashes immediately
        mock_process = MagicMock()
        mock_process.wait = AsyncMock(return_value=1)
        proxy._server_process = mock_process
        proxy.start_server = mock_start_server

        try:
            await asyncio.wait_for(proxy._monitor_server(), timeout=2.0)
        except TimeoutError:
            pass

        assert start_server_called == 1, "Server should have been restarted once"
        assert proxy._initialized is False, "Init state should be reset after restart"
        assert proxy._init_result is None

    @pytest.mark.asyncio
    async def test_max_restarts_exceeded(self):
        """After 3 restarts, proxy should shut down."""
        proxy = _make_proxy()
        proxy.state_dir = Path("/tmp/nonexistent-state")
        proxy._tcp_server = MagicMock()
        proxy._tcp_server.close = MagicMock()

        start_server_calls = 0

        async def mock_start_server():
            nonlocal start_server_calls
            start_server_calls += 1
            new_process = MagicMock()
            new_process.wait = AsyncMock(return_value=1)  # Also crashes
            proxy._server_process = new_process
            proxy._server_reader = asyncio.StreamReader()
            proxy._server_reader.feed_eof()

        # Initial process crashes
        mock_process = MagicMock()
        mock_process.wait = AsyncMock(return_value=1)
        proxy._server_process = mock_process
        proxy.start_server = mock_start_server

        await proxy._monitor_server()

        assert start_server_calls == 3, f"Expected 3 restarts, got {start_server_calls}"
        assert proxy._running is False, "Proxy should stop after max restarts"


# -- Drain: prevents backpressure without client connected --------------------


class TestQueueDrainPreventsBackpressure:
    """_read_server_stdout drains server stdout regardless of client status."""

    @pytest.mark.asyncio
    async def test_queue_drain_prevents_backpressure(self):
        """Messages should be read from stdout even with no client connected."""
        proxy = _make_proxy()
        proxy._current_client = None  # No client

        reader = asyncio.StreamReader()
        msg1 = make_lsp_message(
            {"jsonrpc": "2.0", "method": "$/progress", "params": {}}
        )
        msg2 = make_lsp_message(
            {"jsonrpc": "2.0", "method": "$/progress", "params": {}}
        )
        reader.feed_data(msg1 + msg2)
        reader.feed_eof()
        proxy._server_reader = reader

        await proxy._read_server_stdout()

        count = 0
        while not proxy._server_messages.empty():
            proxy._server_messages.get_nowait()
            count += 1
        # 2 messages + 1 EOF sentinel
        assert count >= 2, "Messages should be drained even without a connected client"


# -- Shutdown cleanup ----------------------------------------------------------


class TestShutdownCleansUpReaderTask:
    """_shutdown should cancel the persistent server reader task."""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_reader_task(self):
        proxy = _make_proxy()
        proxy.state_dir = Path("/tmp/nonexistent-state")

        async def long_running():
            await asyncio.sleep(100)

        proxy._server_reader_task = asyncio.create_task(long_running())

        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        proxy._server_writer = mock_writer
        proxy._server_process = MagicMock()
        proxy._server_process.returncode = 0
        proxy._tcp_server = MagicMock()
        proxy._tcp_server.close = MagicMock()

        await proxy._shutdown()

        assert proxy._server_reader_task.done(), (
            "Reader task should be cancelled during shutdown"
        )


# -- Constructor attributes ----------------------------------------------------


class TestConstructorHasQueueAttributes:
    """LspProxyServer constructor should initialize queue-related attributes."""

    def test_has_server_messages_queue(self):
        proxy = LspProxyServer(
            language="rust",
            workspace="/tmp",
            command='["rust-analyzer"]',
            init_options="{}",
            idle_timeout=300,
            state_dir="/tmp/state",
        )
        assert hasattr(proxy, "_server_messages")
        assert isinstance(proxy._server_messages, asyncio.Queue)

    def test_has_server_reader_task(self):
        proxy = LspProxyServer(
            language="rust",
            workspace="/tmp",
            command='["rust-analyzer"]',
            init_options="{}",
            idle_timeout=300,
            state_dir="/tmp/state",
        )
        assert hasattr(proxy, "_server_reader_task")
        assert proxy._server_reader_task is None
