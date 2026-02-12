# pyright: reportOptionalMemberAccess=false, reportOptionalContextManager=false, reportArgumentType=false
"""LSP Proxy — persistent server wrapper.

Runs as a detached process. Owns an LSP server subprocess (stdio),
listens on TCP localhost for client connections, bridges LSP messages,
and exits after idle timeout with no connected clients.

Usage:
    python -m amplifier_module_tool_lsp.proxy \
        --language rust \
        --workspace /path/to/project \
        --command '["rust-analyzer"]' \
        --init-options '{"cargo": {"allFeatures": true}}' \
        --idle-timeout 300 \
        --state-dir ~/.amplifier/lsp-servers
"""

import argparse
import asyncio
import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path


def log(msg: str) -> None:
    """Log to stderr (goes to log file when launched by LspServerManager)."""
    print(f"[lsp-proxy] {msg}", file=sys.stderr, flush=True)


# ── LSP message framing ──────────────────────────────────────────────────────


async def read_lsp_message(reader: asyncio.StreamReader) -> bytes | None:
    """Read a complete LSP message (header + body) from a stream.

    Returns the raw bytes (header + body) for forwarding, or None on EOF.
    """
    headers = b""
    content_length = 0
    while True:
        line = await reader.readline()
        if not line:
            return None  # EOF
        headers += line
        if line == b"\r\n":
            break  # End of headers
        if line.lower().startswith(b"content-length:"):
            content_length = int(line.split(b":")[1].strip())

    if content_length == 0:
        return None

    body = await reader.readexactly(content_length)
    return headers + body


async def write_lsp_message(writer: asyncio.StreamWriter, data: bytes) -> None:
    """Write raw LSP message bytes to a stream."""
    writer.write(data)
    await writer.drain()


def parse_lsp_body(raw_message: bytes) -> dict | None:
    """Extract and parse the JSON body from a raw LSP message."""
    separator = raw_message.find(b"\r\n\r\n")
    if separator == -1:
        return None
    body = raw_message[separator + 4 :]
    return json.loads(body)


def make_lsp_message(body: dict) -> bytes:
    """Create a raw LSP message from a JSON body."""
    content = json.dumps(body).encode()
    header = f"Content-Length: {len(content)}\r\n\r\n".encode()
    return header + content


# ── Proxy server ──────────────────────────────────────────────────────────────


class LspProxyServer:
    def __init__(
        self, language, workspace, command, init_options, idle_timeout, state_dir
    ):
        self.language = language
        self.workspace = Path(workspace)
        self.command = json.loads(command) if isinstance(command, str) else command
        self.init_options = (
            json.loads(init_options) if isinstance(init_options, str) else init_options
        )
        self.idle_timeout = idle_timeout
        self.state_dir = Path(state_dir)

        self._server_process = None
        self._server_reader = None  # stdout
        self._server_writer = None  # stdin
        self._server_messages: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=200)
        self._server_reader_task: asyncio.Task | None = None
        self._tcp_server = None
        self._port = 0
        self._current_client = None  # (reader, writer) or None
        self._last_client_disconnect = time.time()
        self._init_result = None  # Cached InitializeResult
        self._initialized = False  # Has the server been initialized?
        self._pending_init_id = None  # Request ID of in-flight initialize
        self._running = True
        self._open_documents: set[str] = set()

    # ── Server subprocess ─────────────────────────────────────────────────

    async def start_server(self):
        """Start the LSP server subprocess."""
        log(f"starting server: {self.command} in {self.workspace}")
        self._server_process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr,
            cwd=self.workspace,
        )
        self._server_writer = self._server_process.stdin
        self._server_reader = self._server_process.stdout
        log(f"server started (pid={self._server_process.pid})")

    # ── Persistent server stdout reader ─────────────────────────────────────

    async def _read_server_stdout(self):
        """Persistently read LSP messages from server stdout into a queue.

        Runs for the entire proxy lifetime. Prevents pipe backpressure
        regardless of whether a client is connected. Uses non-cancellable
        reads to prevent stream corruption.
        """
        try:
            while self._running:
                raw = await read_lsp_message(self._server_reader)
                if raw is None:
                    break  # Server EOF

                # Cache init response if applicable
                body = parse_lsp_body(raw)
                if (
                    body
                    and not self._initialized
                    and self._pending_init_id is not None
                    and body.get("id") == self._pending_init_id
                    and "result" in body
                ):
                    self._init_result = body["result"]
                    self._initialized = True
                    self._pending_init_id = None

                # Put in queue — if full, drop oldest to prevent memory growth
                if self._server_messages.full():
                    try:
                        self._server_messages.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                await self._server_messages.put(raw)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            # Signal EOF to any consumer
            try:
                self._server_messages.put_nowait(None)
            except asyncio.QueueFull:
                pass

    # ── TCP listener ──────────────────────────────────────────────────────

    async def start_tcp_server(self):
        """Start TCP server on localhost with auto-assigned port."""
        self._tcp_server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=0,
        )
        self._port = self._tcp_server.sockets[0].getsockname()[1]
        log(f"TCP server listening on 127.0.0.1:{self._port}")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle a single client connection.

        This is a clean per-connection handler. When it returns (client disconnect),
        the proxy continues running and accepting new connections.
        """
        # Wait for slot to be available (sequential client model)
        for _ in range(100):  # Wait up to 10 seconds (100 * 0.1)
            if self._current_client is None:
                break
            await asyncio.sleep(0.1)
        else:
            log("rejecting client (queue timeout)")
            writer.close()
            await writer.wait_closed()
            return

        log("client connected")
        self._current_client = (reader, writer)
        try:
            await self._bridge_client(reader, writer)
        finally:
            # Close all documents the client had open so the server's document
            # state stays clean for the next client (prevents duplicate-open errors)
            try:
                await self._close_tracked_documents()
            except Exception:
                pass

            self._current_client = None
            self._last_client_disconnect = time.time()
            log("client disconnected")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ── Bidirectional bridge ──────────────────────────────────────────────

    async def _bridge_client(self, client_reader, client_writer):
        """Bridge LSP messages between a TCP client and the stdio server.

        Uses an event to signal disconnect instead of cancelling the server reader,
        which would corrupt the shared server stdout stream.
        """
        # Drain stale messages from previous session before starting new forwarders.
        # Without this, old responses/notifications get forwarded to the new client,
        # potentially corrupting its session (e.g., stale id=1 response resolves the
        # new client's initialize request with wrong data).
        while not self._server_messages.empty():
            try:
                self._server_messages.get_nowait()
            except asyncio.QueueEmpty:
                break

        client_done = asyncio.Event()

        client_to_server = asyncio.create_task(
            self._forward_client_to_server(client_reader, client_writer)
        )
        server_to_client = asyncio.create_task(
            self._forward_server_to_client(client_writer, client_done)
        )

        # Wait for the client-to-server direction to finish (client disconnect/exit)
        try:
            await client_to_server
        except Exception:
            pass

        # Signal the server-to-client forwarder to stop
        client_done.set()

        # Wait for it to finish cleanly — give it up to 5 seconds.
        # asyncio.wait (unlike wait_for) does NOT cancel on timeout.
        try:
            await asyncio.wait([server_to_client], timeout=5.0)
        except Exception:
            pass

        # Cancel the forwarder if it's still running after timeout.
        # This is safe because _forward_server_to_client reads from a Queue
        # (not the raw server stdout stream), and Queue.get() handles
        # cancellation cleanly without corrupting any stream framing state.
        if not server_to_client.done():
            server_to_client.cancel()
            try:
                await server_to_client
            except asyncio.CancelledError:
                pass

    async def _forward_client_to_server(self, client_reader, client_writer):
        """Read from TCP client, forward to server stdin."""
        while True:
            raw = await read_lsp_message(client_reader)
            if raw is None:
                return  # Client disconnected

            body = parse_lsp_body(raw)
            if body is None:
                continue

            method = body.get("method")

            # Handle initialize specially — cache or replay
            if method == "initialize":
                if self._initialized:
                    # Server already initialized — return cached result
                    response = {
                        "jsonrpc": "2.0",
                        "id": body.get("id"),
                        "result": self._init_result,
                    }
                    await write_lsp_message(client_writer, make_lsp_message(response))
                    continue
                else:
                    # First initialize — track ID and forward to server
                    self._pending_init_id = body.get("id")
                    self._server_writer.write(raw)
                    await self._server_writer.drain()
                    continue

            # Swallow shutdown/exit from client — don't kill the shared server
            if method == "shutdown":
                response = {"jsonrpc": "2.0", "id": body.get("id"), "result": None}
                await write_lsp_message(client_writer, make_lsp_message(response))
                continue
            if method == "exit":
                return  # Client wants to disconnect

            # Swallow initialized notification if server already initialized
            if method == "initialized" and self._initialized:
                continue

            # Track document open/close for cleanup on disconnect
            if method == "textDocument/didOpen":
                uri = body.get("params", {}).get("textDocument", {}).get("uri")
                if uri:
                    self._open_documents.add(uri)
            elif method == "textDocument/didClose":
                uri = body.get("params", {}).get("textDocument", {}).get("uri")
                if uri:
                    self._open_documents.discard(uri)

            # Forward everything else to server
            self._server_writer.write(raw)
            await self._server_writer.drain()

    async def _forward_server_to_client(self, client_writer, client_done=None):
        """Forward messages from server queue to TCP client.

        Reads from _server_messages queue (not server stdout directly).
        The queue is safe to timeout on — unlike read_lsp_message, cancelling
        queue.get() cannot corrupt stream framing state.
        """
        while True:
            # Check if client has disconnected before blocking on queue
            if client_done is not None and client_done.is_set():
                return

            # Read next message from queue with a timeout so we can check
            # the client_done event periodically
            try:
                raw = await asyncio.wait_for(self._server_messages.get(), timeout=1.0)
            except TimeoutError:
                # No message available yet — loop back to check client_done
                continue

            if raw is None:
                return  # Server EOF

            # Forward to client (skip if client already disconnected)
            if client_done is not None and client_done.is_set():
                return
            try:
                await write_lsp_message(client_writer, raw)
            except (ConnectionError, OSError):
                # Client disconnected while we were writing
                return

    async def _handle_server_request_internally(self, message: dict):
        """Respond to server-initiated requests when no client is connected.

        Mirrors the logic in server.py's _handle_server_request() — accepts all
        requests with appropriate minimal responses so the server doesn't block.
        """
        request_id = message["id"]
        method = message["method"]

        if method == "workspace/configuration":
            items = message.get("params", {}).get("items", [])
            result = [{} for _ in items]
        else:
            # client/registerCapability, window/workDoneProgress/create, and
            # any unknown method — accept with null result
            result = None

        response = make_lsp_message(
            {"jsonrpc": "2.0", "id": request_id, "result": result}
        )
        self._server_writer.write(response)
        await self._server_writer.drain()

    async def _drain_queue_when_idle(self):
        """Background task: handle server messages when no client is connected.

        When a client IS connected, _forward_server_to_client() consumes the queue.
        When no client is connected, THIS task takes over — responding to server-
        initiated requests (so the server doesn't block) and discarding everything
        else (notifications, stale responses).
        """
        while True:
            # When a client is connected, the forwarder handles the queue.
            # Sleep to avoid spinning, then re-check.
            if self._current_client is not None:
                if not self._running:
                    return
                await asyncio.sleep(0.5)
                continue

            # No client connected — drain the queue ourselves
            try:
                raw = await asyncio.wait_for(self._server_messages.get(), timeout=1.0)
            except TimeoutError:
                if not self._running:
                    return
                continue

            if raw is None:
                # Server EOF sentinel — re-queue it for _forward_server_to_client
                # or _monitor_server to see, then exit if shutting down
                try:
                    self._server_messages.put_nowait(None)
                except asyncio.QueueFull:
                    pass
                if not self._running:
                    return
                continue

            # Parse and classify the message
            body = parse_lsp_body(raw)
            if body is None:
                continue

            # Server-initiated request: has both "method" and "id" → must respond
            if "method" in body and "id" in body:
                try:
                    await self._handle_server_request_internally(body)
                except Exception:
                    pass
                continue

            # Everything else (notifications, stale responses) — discard silently

    async def _close_tracked_documents(self):
        """Send textDocument/didClose for all tracked open documents.

        Called on client disconnect so the server's document state stays clean.
        Without this, the next client's didOpen for the same files would be a
        protocol violation (duplicate open) that can crash rust-analyzer.
        """
        for uri in self._open_documents:
            try:
                close_msg = make_lsp_message(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didClose",
                        "params": {"textDocument": {"uri": uri}},
                    }
                )
                self._server_writer.write(close_msg)
                await self._server_writer.drain()
            except Exception:
                pass
        self._open_documents.clear()

    # ── Idle timeout ──────────────────────────────────────────────────────

    async def _idle_monitor(self):
        """Periodically check for idle timeout. Exit if exceeded."""
        while self._running:
            await asyncio.sleep(10)

            if self._current_client is not None:
                continue

            idle_seconds = time.time() - self._last_client_disconnect
            if idle_seconds >= self.idle_timeout:
                await self._shutdown()
                return

    # ── Server health monitoring ──────────────────────────────────────────

    async def _monitor_server(self):
        """Monitor server subprocess. Restart if it dies (up to 3 times)."""
        restart_count = 0
        max_restarts = 3

        while self._running and restart_count < max_restarts:
            if self._server_process is None:
                return
            await self._server_process.wait()

            if not self._running:
                return  # Intentional shutdown

            restart_count += 1
            log(f"server exited unexpectedly (restart {restart_count}/{max_restarts})")

            # Restart the server
            try:
                # Force-clear client slot (old bridge is talking to dead server)
                self._current_client = None

                # Create fresh queue (discard stale EOF sentinels from dead server)
                self._server_messages = asyncio.Queue(maxsize=200)

                # Restart server subprocess
                await self.start_server()

                # Restart the reader task for the new server's stdout
                if self._server_reader_task:
                    self._server_reader_task.cancel()
                    try:
                        await self._server_reader_task
                    except asyncio.CancelledError:
                        pass
                self._server_reader_task = asyncio.create_task(
                    self._read_server_stdout()
                )

                # Reset all init state so next client triggers fresh handshake
                self._initialized = False
                self._init_result = None
                self._pending_init_id = None

                # Update state file with new server PID
                self._write_state_file()

                log("server restarted successfully")
            except Exception as e:
                log(f"server restart failed: {e}")
                break

        # Max restarts exceeded or restart failed
        if self._running:
            log(f"server failed after {restart_count} restarts, shutting down proxy")
            self._remove_state_file()
            if self._tcp_server:
                self._tcp_server.close()
            self._running = False

    # ── Graceful shutdown ─────────────────────────────────────────────────

    async def _shutdown(self):
        """Gracefully shutdown server and clean up."""
        log("shutting down")
        self._running = False

        # Stop the persistent reader task
        if self._server_reader_task:
            self._server_reader_task.cancel()
            try:
                await self._server_reader_task
            except asyncio.CancelledError:
                pass

        # Shutdown LSP server
        try:
            shutdown_msg = make_lsp_message(
                {"jsonrpc": "2.0", "id": 999999, "method": "shutdown", "params": {}}
            )
            self._server_writer.write(shutdown_msg)
            await self._server_writer.drain()
            await asyncio.sleep(0.5)
            exit_msg = make_lsp_message(
                {"jsonrpc": "2.0", "method": "exit", "params": {}}
            )
            self._server_writer.write(exit_msg)
            await self._server_writer.drain()
        except Exception:
            pass

        # Kill server process if still running
        if self._server_process and self._server_process.returncode is None:
            self._server_process.kill()
            try:
                await asyncio.wait_for(self._server_process.communicate(), timeout=5.0)
            except Exception:
                pass

        # Close TCP server
        if self._tcp_server:
            self._tcp_server.close()

        # Remove state file
        self._remove_state_file()

    # ── State file ────────────────────────────────────────────────────────

    def _state_file_path(self) -> Path:
        """Compute the state file path for this proxy."""
        canonical = os.path.realpath(str(self.workspace))
        hash8 = hashlib.sha256(canonical.encode()).hexdigest()[:8]
        return self.state_dir / f"{self.language}-{hash8}.json"

    def _write_state_file(self):
        """Write state file atomically (temp file + rename)."""
        state = {
            "language": self.language,
            "workspace_root": str(self.workspace),
            "port": self._port,
            "proxy_pid": os.getpid(),
            "server_pid": self._server_process.pid if self._server_process else None,
            "server_command": self.command,
            "lifecycle": "timeout" if self.idle_timeout > 0 else "persistent",
            "idle_timeout": self.idle_timeout,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        self.state_dir.mkdir(parents=True, exist_ok=True)
        state_path = self._state_file_path()
        tmp_path = state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, indent=2))
        tmp_path.rename(state_path)  # Atomic on POSIX

    def _remove_state_file(self):
        """Remove the state file."""
        try:
            self._state_file_path().unlink(missing_ok=True)
        except Exception:
            pass

    # ── Main entry point ──────────────────────────────────────────────────

    async def run(self):
        """Main proxy event loop."""
        await self.start_server()
        # Start persistent reader AFTER server starts
        self._server_reader_task = asyncio.create_task(self._read_server_stdout())
        await self.start_tcp_server()
        self._write_state_file()

        # Handle SIGTERM gracefully
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown()))

        # Run idle monitor, server monitor, and TCP server concurrently
        async with self._tcp_server:
            await asyncio.gather(
                self._tcp_server.serve_forever(),
                self._idle_monitor(),
                self._monitor_server(),
                self._drain_queue_when_idle(),
            )


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="LSP Proxy")
    parser.add_argument("--language", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--command", required=True, help="JSON array of server command")
    parser.add_argument(
        "--init-options", default="{}", help="JSON object of initializationOptions"
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=300,
        help="Seconds to wait before shutdown when idle",
    )
    parser.add_argument("--state-dir", required=True, help="Directory for state files")
    return parser.parse_args()


def main():
    args = parse_args()
    proxy = LspProxyServer(
        language=args.language,
        workspace=args.workspace,
        command=args.command,
        init_options=args.init_options,
        idle_timeout=args.idle_timeout,
        state_dir=args.state_dir,
    )
    asyncio.run(proxy.run())


if __name__ == "__main__":
    main()
