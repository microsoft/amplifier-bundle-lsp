# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportOptionalSubscript=false
"""Tests for proxy reconnection bugs.

Bug 1: stderr pipe never drained — rust-analyzer deadlocks after 64KB of stderr output.
Bug 2: Server-initiated requests go unanswered between clients — server crashes.
Bug 3: Session cleanup issues — protocol violations on reconnect.
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_tool_lsp.proxy import (
    LspProxyServer,
    make_lsp_message,
    parse_lsp_body,
)


# —— Helpers ——————————————————————————————————————————————————————————


def _make_proxy():
    """Create a proxy with all attributes for testing (bypasses __init__)."""
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
    proxy._open_documents = set()
    proxy.language = "rust"
    proxy.workspace = Path("/tmp/test-ws")
    proxy.command = ["rust-analyzer"]
    proxy.idle_timeout = 300
    proxy.state_dir = Path("/tmp/state")
    proxy.init_options = {}
    return proxy


# —— Bug 1: stderr goes to sys.stderr, not PIPE ——————————————————————————————


class TestStderrRedirect:
    """rust-analyzer stderr must go to sys.stderr, not an unread PIPE."""

    @pytest.mark.asyncio
    async def test_start_server_uses_sys_stderr_not_pipe(self):
        """start_server() must pass stderr=sys.stderr to avoid pipe deadlock."""
        proxy = _make_proxy()

        fake_process = MagicMock()
        fake_process.pid = 12345
        fake_process.stdin = MagicMock()
        fake_process.stdout = MagicMock()

        with patch(
            "asyncio.create_subprocess_exec", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.return_value = fake_process
            await proxy.start_server()

            # Verify stderr kwarg is sys.stderr, NOT asyncio.subprocess.PIPE
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["stderr"] is sys.stderr, (
                f"Expected stderr=sys.stderr, got stderr={call_kwargs['stderr']!r}. "
                "Using PIPE without a drain task causes a 64KB buffer deadlock."
            )


# ── Bug 2: Server-initiated requests answered when no client connected ───────


class TestServerRequestHandling:
    """Proxy must respond to server-initiated requests even when no client is connected."""

    @pytest.mark.asyncio
    async def test_handle_register_capability(self):
        """client/registerCapability should get a null-result response."""
        proxy = _make_proxy()

        written = []
        writer = MagicMock()
        writer.write = MagicMock(side_effect=lambda data: written.append(data))
        writer.drain = AsyncMock()
        proxy._server_writer = writer

        await proxy._handle_server_request_internally(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "client/registerCapability",
                "params": {},
            }
        )

        assert len(written) == 1
        body = parse_lsp_body(written[0])
        assert body["id"] == 1
        assert body["result"] is None

    @pytest.mark.asyncio
    async def test_handle_workspace_configuration(self):
        """workspace/configuration should return one empty dict per requested item."""
        proxy = _make_proxy()

        written = []
        writer = MagicMock()
        writer.write = MagicMock(side_effect=lambda data: written.append(data))
        writer.drain = AsyncMock()
        proxy._server_writer = writer

        await proxy._handle_server_request_internally(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "workspace/configuration",
                "params": {
                    "items": [{"section": "rust-analyzer"}, {"section": "editor"}]
                },
            }
        )

        assert len(written) == 1
        body = parse_lsp_body(written[0])
        assert body["id"] == 2
        assert body["result"] == [{}, {}]

    @pytest.mark.asyncio
    async def test_handle_work_done_progress_create(self):
        """window/workDoneProgress/create should get a null-result response."""
        proxy = _make_proxy()

        written = []
        writer = MagicMock()
        writer.write = MagicMock(side_effect=lambda data: written.append(data))
        writer.drain = AsyncMock()
        proxy._server_writer = writer

        await proxy._handle_server_request_internally(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "window/workDoneProgress/create",
                "params": {"token": "ra/1"},
            }
        )

        assert len(written) == 1
        body = parse_lsp_body(written[0])
        assert body["id"] == 3
        assert body["result"] is None

    @pytest.mark.asyncio
    async def test_handle_unknown_request_accepts(self):
        """Unknown server-initiated requests get accepted with null result (never rejected)."""
        proxy = _make_proxy()

        written = []
        writer = MagicMock()
        writer.write = MagicMock(side_effect=lambda data: written.append(data))
        writer.drain = AsyncMock()
        proxy._server_writer = writer

        await proxy._handle_server_request_internally(
            {"jsonrpc": "2.0", "id": 99, "method": "some/unknown/method", "params": {}}
        )

        assert len(written) == 1
        body = parse_lsp_body(written[0])
        assert body["id"] == 99
        assert body["result"] is None


class TestDrainQueueWhenIdle:
    """_drain_queue_when_idle must respond to server requests when no client is connected."""

    @pytest.mark.asyncio
    async def test_responds_to_server_request_when_no_client(self):
        """Server-initiated request in queue gets a response when no client connected."""
        proxy = _make_proxy()
        proxy._current_client = None

        written = []
        writer = MagicMock()
        writer.write = MagicMock(side_effect=lambda data: written.append(data))
        writer.drain = AsyncMock()
        proxy._server_writer = writer

        # Put a server-initiated request in the queue
        request_msg = make_lsp_message(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "client/registerCapability",
                "params": {},
            }
        )
        await proxy._server_messages.put(request_msg)
        # Put a None sentinel to make the drain task exit its inner loop
        await proxy._server_messages.put(None)
        # Set _running to False so the outer loop exits after processing
        proxy._running = False

        await proxy._drain_queue_when_idle()

        # Should have responded to the server request
        assert len(written) == 1
        body = parse_lsp_body(written[0])
        assert body["id"] == 10
        assert body["result"] is None

    @pytest.mark.asyncio
    async def test_discards_notifications_when_no_client(self):
        """Server notifications (no id) should be silently discarded when idle."""
        proxy = _make_proxy()
        proxy._current_client = None

        written = []
        writer = MagicMock()
        writer.write = MagicMock(side_effect=lambda data: written.append(data))
        writer.drain = AsyncMock()
        proxy._server_writer = writer

        # Put a notification (method but no id) in the queue
        notification_msg = make_lsp_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {},
            }
        )
        await proxy._server_messages.put(notification_msg)
        await proxy._server_messages.put(None)
        proxy._running = False

        await proxy._drain_queue_when_idle()

        # Should NOT have written anything — notifications are discarded
        assert len(written) == 0

    @pytest.mark.asyncio
    async def test_discards_responses_when_no_client(self):
        """Stale response messages (id + result, no method) should be discarded when idle."""
        proxy = _make_proxy()
        proxy._current_client = None

        written = []
        writer = MagicMock()
        writer.write = MagicMock(side_effect=lambda data: written.append(data))
        writer.drain = AsyncMock()
        proxy._server_writer = writer

        # Put a response (id + result, no method) in the queue
        response_msg = make_lsp_message(
            {"jsonrpc": "2.0", "id": 5, "result": {"some": "data"}}
        )
        await proxy._server_messages.put(response_msg)
        await proxy._server_messages.put(None)
        proxy._running = False

        await proxy._drain_queue_when_idle()

        # Should NOT have written anything — stale responses are discarded
        assert len(written) == 0

    @pytest.mark.asyncio
    async def test_skips_drain_when_client_connected(self):
        """When a client IS connected, the drain task should yield (not consume messages)."""
        proxy = _make_proxy()
        proxy._current_client = ("reader", "writer")  # Client connected

        # Put a message in the queue
        msg = make_lsp_message(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "client/registerCapability",
                "params": {},
            }
        )
        await proxy._server_messages.put(msg)

        # Run drain briefly — it should sleep and not consume the message
        proxy._running = False  # Will exit after one sleep cycle
        await proxy._drain_queue_when_idle()

        # Message should still be in the queue (not consumed)
        assert not proxy._server_messages.empty()


# ── Bug 3a: Abandoned forwarder tasks cancelled ─────────────────────────────────


class TestForwarderCancellation:
    """Abandoned server-to-client forwarder must be cancelled after timeout."""

    @pytest.mark.asyncio
    async def test_server_to_client_cancelled_after_bridge_ends(self):
        """After _bridge_client returns, server_to_client task should be cancelled."""
        proxy = _make_proxy()

        # Set up a client reader that immediately returns EOF (client disconnect)
        client_reader = asyncio.StreamReader()
        client_reader.feed_eof()

        client_writer = MagicMock()
        client_writer.write = MagicMock()
        client_writer.drain = AsyncMock()

        # Capture tasks created so we can check the server_to_client task state
        original_create_task = asyncio.create_task
        tasks_created = []

        def tracking_create_task(coro):
            task = original_create_task(coro)
            tasks_created.append(task)
            return task

        with patch("asyncio.create_task", side_effect=tracking_create_task):
            # Use a very short timeout so we don't wait 5s in the test
            with patch("asyncio.wait", new_callable=AsyncMock) as mock_wait:
                # Simulate timeout (return empty done set)
                mock_wait.return_value = (set(), set())
                await proxy._bridge_client(client_reader, client_writer)

        # The second task created is server_to_client — it should be cancelled
        if len(tasks_created) >= 2:
            server_to_client_task = tasks_created[1]
            assert server_to_client_task.cancelled() or server_to_client_task.done()


# ── Bug 3b: Stale queue messages cleared on new connection ───────────────────────


class TestStaleQueueDrain:
    """Queue must be drained at the start of each new client session."""

    @pytest.mark.asyncio
    async def test_stale_messages_cleared_before_new_session(self):
        """Stale messages from previous session should be gone when bridge starts."""
        proxy = _make_proxy()

        # Pre-fill queue with stale messages from "previous session"
        stale_response = make_lsp_message(
            {"jsonrpc": "2.0", "id": 1, "result": {"stale": True}}
        )
        stale_notification = make_lsp_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {},
            }
        )
        await proxy._server_messages.put(stale_response)
        await proxy._server_messages.put(stale_notification)
        assert proxy._server_messages.qsize() == 2

        # Client that immediately disconnects
        client_reader = asyncio.StreamReader()
        client_reader.feed_eof()

        client_writer = MagicMock()
        client_writer.write = MagicMock()
        client_writer.drain = AsyncMock()

        await proxy._bridge_client(client_reader, client_writer)

        # Queue should now be empty (stale messages drained before session started)
        assert proxy._server_messages.empty(), (
            f"Queue still has {proxy._server_messages.qsize()} stale messages"
        )


# ── Bug 3c: Open documents tracked and closed on disconnect ─────────────────────


class TestDocumentTracking:
    """Proxy must track open documents and close them on client disconnect."""

    @pytest.mark.asyncio
    async def test_did_open_tracked(self):
        """textDocument/didOpen should add URI to _open_documents set."""
        proxy = _make_proxy()

        server_written = []
        server_writer = MagicMock()
        server_writer.write = MagicMock(
            side_effect=lambda data: server_written.append(data)
        )
        server_writer.drain = AsyncMock()
        proxy._server_writer = server_writer

        # Build a client stream with didOpen followed by EOF
        client_reader = asyncio.StreamReader()
        did_open = make_lsp_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": "file:///tmp/test.rs",
                        "languageId": "rust",
                        "version": 1,
                        "text": "fn main() {}",
                    }
                },
            }
        )
        client_reader.feed_data(did_open)
        client_reader.feed_eof()

        client_writer = MagicMock()
        client_writer.write = MagicMock()
        client_writer.drain = AsyncMock()

        await proxy._forward_client_to_server(client_reader, client_writer)

        assert "file:///tmp/test.rs" in proxy._open_documents

    @pytest.mark.asyncio
    async def test_did_close_untracked(self):
        """textDocument/didClose should remove URI from _open_documents set."""
        proxy = _make_proxy()
        proxy._open_documents = {"file:///tmp/test.rs"}

        server_written = []
        server_writer = MagicMock()
        server_writer.write = MagicMock(
            side_effect=lambda data: server_written.append(data)
        )
        server_writer.drain = AsyncMock()
        proxy._server_writer = server_writer

        client_reader = asyncio.StreamReader()
        did_close = make_lsp_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didClose",
                "params": {"textDocument": {"uri": "file:///tmp/test.rs"}},
            }
        )
        client_reader.feed_data(did_close)
        client_reader.feed_eof()

        client_writer = MagicMock()
        client_writer.write = MagicMock()
        client_writer.drain = AsyncMock()

        await proxy._forward_client_to_server(client_reader, client_writer)

        assert "file:///tmp/test.rs" not in proxy._open_documents

    @pytest.mark.asyncio
    async def test_disconnect_sends_did_close_for_all_open_docs(self):
        """On client disconnect, proxy must send didClose for every tracked document."""
        proxy = _make_proxy()
        proxy._open_documents = {"file:///tmp/a.rs", "file:///tmp/b.rs"}

        server_written = []
        server_writer = MagicMock()
        server_writer.write = MagicMock(
            side_effect=lambda data: server_written.append(data)
        )
        server_writer.drain = AsyncMock()
        proxy._server_writer = server_writer

        # Client that immediately disconnects
        client_reader = asyncio.StreamReader()
        client_reader.feed_eof()

        client_writer = MagicMock()
        client_writer.write = MagicMock()
        client_writer.drain = AsyncMock()
        client_writer.close = MagicMock()
        client_writer.wait_closed = AsyncMock()

        proxy._current_client = (client_reader, client_writer)
        try:
            await proxy._bridge_client(client_reader, client_writer)
        finally:
            # Simulate the _handle_client finally block
            await proxy._close_tracked_documents()
            proxy._current_client = None

        # Should have sent didClose for both files
        close_uris = set()
        for raw in server_written:
            body = parse_lsp_body(raw)
            if body and body.get("method") == "textDocument/didClose":
                close_uris.add(body["params"]["textDocument"]["uri"])

        assert close_uris == {"file:///tmp/a.rs", "file:///tmp/b.rs"}
        # _open_documents should be cleared
        assert len(proxy._open_documents) == 0

    @pytest.mark.asyncio
    async def test_disconnect_clears_open_documents_set(self):
        """After disconnect cleanup, _open_documents should be empty."""
        proxy = _make_proxy()
        proxy._open_documents = {
            "file:///tmp/a.rs",
            "file:///tmp/b.rs",
            "file:///tmp/c.rs",
        }

        server_writer = MagicMock()
        server_writer.write = MagicMock()
        server_writer.drain = AsyncMock()
        proxy._server_writer = server_writer

        await proxy._close_tracked_documents()

        assert len(proxy._open_documents) == 0
