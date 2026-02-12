# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalSubscript=false
"""Tests for proxy bugs found during real-world testing.

Bug 1 (P0): Proxy subprocess can't find its own module — missing PYTHONPATH.
Bug 2 (P1): Proxy dies after first client disconnects.
Bug 3 (P2): Workspace detection finds crate-level, not workspace-root Cargo.toml.
Bug 4 (P3): Proxy errors invisible — stderr to DEVNULL.
"""

import asyncio
import json
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_tool_lsp.server import LspServerManager


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_manager():
    """Create an LspServerManager without triggering __init__ side effects."""
    mgr = LspServerManager.__new__(LspServerManager)
    mgr._servers = {}
    mgr._timeout = 30.0
    mgr._on_shutdown = None
    return mgr


# ── Bug 1: PYTHONPATH passed to proxy subprocess ────────────────────────────


class TestPythonPathInProxy:
    """_start_proxy must pass PYTHONPATH so the detached subprocess can find
    amplifier_module_tool_lsp."""

    @pytest.mark.asyncio
    async def test_start_proxy_passes_env_with_pythonpath(self, tmp_path):
        """Popen should receive env dict containing PYTHONPATH from sys.path."""
        mgr = _make_manager()
        mgr.STATE_DIR = tmp_path

        # Write a fake state file after Popen is called to satisfy the wait loop
        state_path = mgr._state_file_path("rust", Path("/tmp/ws"))

        # Mock process that appears alive
        alive_process = MagicMock()
        alive_process.poll.return_value = None

        def fake_popen(*args, **kwargs):
            # Write state file so the wait loop exits
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(
                    {
                        "port": 12345,
                        "proxy_pid": 99999,
                        "language": "rust",
                        "workspace_root": "/tmp/ws",
                    }
                )
            )
            return alive_process

        with (
            patch("subprocess.Popen", side_effect=fake_popen) as mock_popen,
            patch.object(
                mgr,
                "_read_state",
                return_value={
                    "port": 12345,
                    "proxy_pid": 99999,
                    "language": "rust",
                    "workspace_root": "/tmp/ws",
                },
            ),
        ):
            await mgr._start_proxy(
                language="rust",
                workspace=Path("/tmp/ws"),
                server_config={"command": ["rust-analyzer"]},
                init_options={},
            )

        # Verify Popen was called with env containing PYTHONPATH
        mock_popen.assert_called_once()
        call_kwargs = mock_popen.call_args[1]
        assert "env" in call_kwargs, "Popen must be called with env= kwarg"
        env = call_kwargs["env"]
        assert "PYTHONPATH" in env, "env must contain PYTHONPATH"
        # PYTHONPATH should be a non-empty string
        assert len(env["PYTHONPATH"]) > 0


# ── Bug 2: Proxy survives client disconnect ─────────────────────────────────


class TestProxySurvivesDisconnect:
    """After _handle_client returns (client disconnect), the proxy must remain
    running and the TCP server must still be listening."""

    @pytest.mark.asyncio
    async def test_proxy_running_after_client_disconnect(self):
        """After _handle_client completes, _running should still be True."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        proxy._current_client = None
        proxy._last_client_disconnect = time.time()
        proxy._initialized = True
        proxy._init_result = {"capabilities": {}}
        proxy._pending_init_id = None
        proxy._server_messages = asyncio.Queue(maxsize=200)
        proxy._open_documents = set()

        # Mock server writer
        mock_server_writer = MagicMock()
        mock_server_writer.write = MagicMock()
        mock_server_writer.drain = AsyncMock()
        proxy._server_writer = mock_server_writer

        # Create a mock server reader that returns None (simulating no data
        # from server during this client's session — the client disconnects first)
        mock_server_reader = asyncio.StreamReader()
        proxy._server_reader = mock_server_reader

        # Create client streams — client sends initialize + exit then disconnects
        from amplifier_module_tool_lsp.proxy import make_lsp_message

        init_msg = make_lsp_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        exit_msg = make_lsp_message({"jsonrpc": "2.0", "method": "exit"})

        client_reader = asyncio.StreamReader()
        client_reader.feed_data(init_msg + exit_msg)
        client_reader.feed_eof()

        client_writer = MagicMock()
        client_writer.write = MagicMock()
        client_writer.drain = AsyncMock()
        client_writer.close = MagicMock()
        client_writer.wait_closed = AsyncMock()

        # Feed EOF to server reader so the server_to_client task can complete
        # (rather than blocking forever)
        mock_server_reader.feed_eof()

        await proxy._handle_client(client_reader, client_writer)

        # Key assertion: proxy is still running after client disconnect
        assert proxy._running is True, (
            "Proxy must remain running after client disconnects"
        )
        assert proxy._current_client is None, (
            "Current client should be cleared after disconnect"
        )

    @pytest.mark.asyncio
    async def test_server_reader_not_closed_after_client_disconnect(self):
        """The server process stdout must not be closed when a client disconnects."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        proxy._current_client = None
        proxy._last_client_disconnect = time.time()
        proxy._initialized = True
        proxy._init_result = {"capabilities": {}}
        proxy._pending_init_id = None
        proxy._server_messages = asyncio.Queue(maxsize=200)
        proxy._open_documents = set()

        # Mock server writer/reader
        mock_server_writer = MagicMock()
        mock_server_writer.write = MagicMock()
        mock_server_writer.drain = AsyncMock()
        proxy._server_writer = mock_server_writer

        mock_server_reader = asyncio.StreamReader()
        proxy._server_reader = mock_server_reader

        from amplifier_module_tool_lsp.proxy import make_lsp_message

        exit_msg = make_lsp_message({"jsonrpc": "2.0", "method": "exit"})
        client_reader = asyncio.StreamReader()
        client_reader.feed_data(exit_msg)
        client_reader.feed_eof()

        client_writer = MagicMock()
        client_writer.write = MagicMock()
        client_writer.drain = AsyncMock()
        client_writer.close = MagicMock()
        client_writer.wait_closed = AsyncMock()

        # Feed EOF so server_to_client completes
        mock_server_reader.feed_eof()

        await proxy._handle_client(client_reader, client_writer)

        # The server reader should still be usable (not permanently broken)
        # We verify this by checking the proxy still has a valid _server_reader
        assert proxy._server_reader is not None
        assert proxy._running is True


# ── Bug 3: Workspace detection prefers workspace root ────────────────────────


class TestWorkspaceDetectionCargo:
    """_find_workspace should prefer Cargo.toml with [workspace] section
    over crate-level Cargo.toml."""

    def _make_tool(self):
        """Create a tool with Rust language config."""
        from amplifier_module_tool_lsp.tool import LspTool

        return LspTool(
            {
                "languages": {
                    "rust": {
                        "extensions": [".rs"],
                        "workspace_markers": ["Cargo.toml", ".git"],
                        "server": {"command": ["rust-analyzer"]},
                    }
                }
            }
        )

    def test_finds_workspace_root_not_crate(self, tmp_path):
        """With nested Cargo.toml files, should return the one with [workspace]."""
        # Create workspace structure:
        # tmp_path/
        #   Cargo.toml          (contains [workspace])
        #   crates/
        #     sub/
        #       Cargo.toml      (crate-level, no [workspace])
        #       src/
        #         lib.rs
        workspace_root = tmp_path / "project"
        workspace_root.mkdir()
        (workspace_root / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/*"]\n'
        )

        crate_dir = workspace_root / "crates" / "sub"
        crate_dir.mkdir(parents=True)
        (crate_dir / "Cargo.toml").write_text(
            '[package]\nname = "sub"\nversion = "0.1.0"\n'
        )

        src_dir = crate_dir / "src"
        src_dir.mkdir()
        lib_rs = src_dir / "lib.rs"
        lib_rs.write_text("pub fn hello() {}\n")

        tool = self._make_tool()
        lang_config = tool._languages["rust"]
        result = tool._find_workspace(str(lib_rs), lang_config)

        assert result == workspace_root, (
            f"Expected workspace root {workspace_root}, got {result}. "
            "Should find Cargo.toml with [workspace], not crate-level Cargo.toml."
        )

    def test_fallback_single_crate_no_workspace(self, tmp_path):
        """Single Cargo.toml without [workspace] should still be found."""
        # Create simple project:
        # tmp_path/
        #   project/
        #     Cargo.toml      (no [workspace])
        #     src/
        #       main.rs
        project = tmp_path / "project"
        project.mkdir()
        (project / "Cargo.toml").write_text(
            '[package]\nname = "myapp"\nversion = "0.1.0"\n'
        )

        src_dir = project / "src"
        src_dir.mkdir()
        main_rs = src_dir / "main.rs"
        main_rs.write_text("fn main() {}\n")

        tool = self._make_tool()
        lang_config = tool._languages["rust"]
        result = tool._find_workspace(str(main_rs), lang_config)

        assert result == project, (
            f"Expected {project}, got {result}. "
            "Single-crate project should still find the Cargo.toml directory."
        )

    def test_git_marker_used_as_fallback(self, tmp_path):
        """When .git exists but no [workspace] in Cargo.toml, .git dir wins."""
        # Create:
        # tmp_path/
        #   .git/
        #   crates/sub/Cargo.toml  (no [workspace])
        #   crates/sub/src/lib.rs
        root = tmp_path / "project"
        root.mkdir()
        (root / ".git").mkdir()

        crate = root / "crates" / "sub"
        crate.mkdir(parents=True)
        (crate / "Cargo.toml").write_text('[package]\nname = "sub"\n')

        src = crate / "src"
        src.mkdir()
        lib_rs = src / "lib.rs"
        lib_rs.write_text("")

        tool = self._make_tool()
        lang_config = tool._languages["rust"]
        result = tool._find_workspace(str(lib_rs), lang_config)

        # Should find .git directory at root, not stop at crate-level Cargo.toml
        assert result == root, (
            f"Expected {root} (has .git), got {result}. "
            ".git should be preferred over crate-level Cargo.toml."
        )


class TestWorkspaceDetectionPython:
    """Python projects should still work with the updated workspace detection."""

    def _make_tool(self):
        from amplifier_module_tool_lsp.tool import LspTool

        return LspTool(
            {
                "languages": {
                    "python": {
                        "extensions": [".py"],
                        "workspace_markers": ["pyproject.toml", ".git"],
                        "server": {"command": ["pyright-langserver", "--stdio"]},
                    }
                }
            }
        )

    def test_python_finds_pyproject_toml(self, tmp_path):
        """Python projects find pyproject.toml as before."""
        project = tmp_path / "myproject"
        project.mkdir()
        (project / "pyproject.toml").write_text("[project]\nname = 'myproject'\n")

        src = project / "src" / "myproject"
        src.mkdir(parents=True)
        py_file = src / "main.py"
        py_file.write_text("print('hello')\n")

        tool = self._make_tool()
        lang_config = tool._languages["python"]
        result = tool._find_workspace(str(py_file), lang_config)

        assert result == project


# ── Bug 4: Proxy stderr goes to log file ─────────────────────────────────────


class TestProxyStderrLogging:
    """_start_proxy should redirect stderr to a log file, not DEVNULL."""

    @pytest.mark.asyncio
    async def test_start_proxy_creates_log_directory(self, tmp_path):
        """A logs/ subdirectory should be created in STATE_DIR."""
        mgr = _make_manager()
        mgr.STATE_DIR = tmp_path

        state_path = mgr._state_file_path("rust", Path("/tmp/ws"))

        # Mock process that appears alive
        alive_process = MagicMock()
        alive_process.poll.return_value = None

        def fake_popen(*args, **kwargs):
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(
                    {
                        "port": 12345,
                        "proxy_pid": 99999,
                        "language": "rust",
                        "workspace_root": "/tmp/ws",
                    }
                )
            )
            return alive_process

        with (
            patch("subprocess.Popen", side_effect=fake_popen),
            patch.object(
                mgr,
                "_read_state",
                return_value={
                    "port": 12345,
                    "proxy_pid": 99999,
                    "language": "rust",
                    "workspace_root": "/tmp/ws",
                },
            ),
        ):
            await mgr._start_proxy(
                language="rust",
                workspace=Path("/tmp/ws"),
                server_config={"command": ["rust-analyzer"]},
                init_options={},
            )

        log_dir = tmp_path / "logs"
        assert log_dir.exists(), "logs/ directory should be created"
        assert log_dir.is_dir()

    @pytest.mark.asyncio
    async def test_start_proxy_stderr_not_devnull(self, tmp_path):
        """Popen stderr should NOT be subprocess.DEVNULL."""
        mgr = _make_manager()
        mgr.STATE_DIR = tmp_path

        state_path = mgr._state_file_path("rust", Path("/tmp/ws"))

        # Mock process that appears alive
        alive_process = MagicMock()
        alive_process.poll.return_value = None

        def fake_popen(*args, **kwargs):
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(
                    {
                        "port": 12345,
                        "proxy_pid": 99999,
                        "language": "rust",
                        "workspace_root": "/tmp/ws",
                    }
                )
            )
            return alive_process

        with (
            patch("subprocess.Popen", side_effect=fake_popen) as mock_popen,
            patch.object(
                mgr,
                "_read_state",
                return_value={
                    "port": 12345,
                    "proxy_pid": 99999,
                    "language": "rust",
                    "workspace_root": "/tmp/ws",
                },
            ),
        ):
            await mgr._start_proxy(
                language="rust",
                workspace=Path("/tmp/ws"),
                server_config={"command": ["rust-analyzer"]},
                init_options={},
            )

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs.get("stderr") != subprocess.DEVNULL, (
            "stderr should NOT be DEVNULL — it should go to a log file"
        )


class TestProxyLogFunction:
    """proxy.py should have a log() function for stderr logging."""

    def test_log_function_exists(self):
        from amplifier_module_tool_lsp.proxy import log

        assert callable(log)

    def test_log_writes_to_stderr(self, capsys):
        """log() should write to stderr with [lsp-proxy] prefix."""
        from amplifier_module_tool_lsp.proxy import log

        log("test message")
        captured = capsys.readouterr()
        assert "[lsp-proxy]" in captured.err
        assert "test message" in captured.err


# ── Bug 5 (P0): Don't cancel server-to-client forwarder ──────────────────────


class TestServerForwarderNotCancelled:
    """_bridge_client must not cancel the server-to-client forwarder task.

    Cancelling it corrupts the shared server stdout StreamReader because
    read_lsp_message may be partway through reading headers + body.
    """

    @pytest.mark.asyncio
    async def test_bridge_does_not_cancel_slow_server_forwarder(self):
        """Even when server_to_client is slow to exit, it must not be cancelled.

        With the buggy code, _bridge_client cancels server_to_client after 2s.
        The fix waits up to 5s (using asyncio.wait which never cancels).
        """
        from amplifier_module_tool_lsp.proxy import LspProxyServer, make_lsp_message

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        proxy._current_client = None
        proxy._last_client_disconnect = time.time()
        proxy._initialized = True
        proxy._init_result = {"capabilities": {}}
        proxy._pending_init_id = None
        proxy._server_messages = asyncio.Queue(maxsize=200)
        proxy._open_documents = set()

        mock_server_writer = MagicMock()
        mock_server_writer.write = MagicMock()
        mock_server_writer.drain = AsyncMock()
        proxy._server_writer = mock_server_writer

        was_cancelled = False

        async def slow_server_forwarder(writer, client_done):
            """Simulates a forwarder that takes 3s to exit after client_done."""
            nonlocal was_cancelled
            try:
                # Wait for client disconnect signal
                while not client_done.is_set():
                    await asyncio.sleep(0.1)
                # Simulate finishing current message read (takes time)
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                was_cancelled = True
                raise

        proxy._forward_server_to_client = slow_server_forwarder

        exit_msg = make_lsp_message({"jsonrpc": "2.0", "method": "exit"})
        client_reader = asyncio.StreamReader()
        client_reader.feed_data(exit_msg)
        client_reader.feed_eof()

        client_writer = MagicMock()
        client_writer.write = MagicMock()
        client_writer.drain = AsyncMock()

        await asyncio.wait_for(
            proxy._bridge_client(client_reader, client_writer), timeout=10
        )

        assert not was_cancelled, (
            "server-to-client forwarder was cancelled — "
            "this corrupts the shared server stdout reader"
        )

    @pytest.mark.asyncio
    async def test_server_reader_usable_across_reconnections(self):
        """After client disconnects, server reader must still work for next client."""
        from amplifier_module_tool_lsp.proxy import (
            LspProxyServer,
            make_lsp_message,
            parse_lsp_body,
            read_lsp_message,
        )

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        proxy._current_client = None
        proxy._last_client_disconnect = time.time()
        proxy._initialized = True
        proxy._init_result = {"capabilities": {}}
        proxy._pending_init_id = None
        proxy._server_messages = asyncio.Queue(maxsize=200)
        proxy._open_documents = set()

        mock_server_writer = MagicMock()
        mock_server_writer.write = MagicMock()
        mock_server_writer.drain = AsyncMock()
        proxy._server_writer = mock_server_writer

        server_reader = asyncio.StreamReader()
        proxy._server_reader = server_reader

        # Client 1: connect and immediately exit
        exit_msg = make_lsp_message({"jsonrpc": "2.0", "method": "exit"})
        c1_reader = asyncio.StreamReader()
        c1_reader.feed_data(exit_msg)
        c1_reader.feed_eof()

        c1_writer = MagicMock()
        c1_writer.write = MagicMock()
        c1_writer.drain = AsyncMock()
        c1_writer.close = MagicMock()
        c1_writer.wait_closed = AsyncMock()

        await asyncio.wait_for(proxy._handle_client(c1_reader, c1_writer), timeout=10)

        assert proxy._running is True
        assert proxy._current_client is None

        # Server reader should still be usable — feed a message and read it back
        test_msg = make_lsp_message(
            {"jsonrpc": "2.0", "id": 99, "result": {"test": True}}
        )
        server_reader.feed_data(test_msg)
        server_reader.feed_eof()

        result = await asyncio.wait_for(read_lsp_message(server_reader), timeout=2)
        assert result is not None
        body = parse_lsp_body(result)
        assert body["id"] == 99
        assert body["result"]["test"] is True


# ── Bug 6 (P0): Queue second client instead of rejecting ─────────────────────


class TestClientQueuing:
    """When a client is already connected, new connections should queue
    instead of being immediately rejected."""

    @pytest.mark.asyncio
    async def test_second_client_waits_for_first_to_disconnect(self):
        """Second client must be served after first disconnects, not rejected."""
        from amplifier_module_tool_lsp.proxy import LspProxyServer

        proxy = LspProxyServer.__new__(LspProxyServer)
        proxy._running = True
        proxy._current_client = (MagicMock(), MagicMock())  # First client active
        proxy._last_client_disconnect = time.time()

        bridge_called = False

        async def mock_bridge(reader, writer):
            nonlocal bridge_called
            bridge_called = True

        proxy._bridge_client = mock_bridge

        # Schedule first client to "disconnect" after 0.2s
        async def disconnect_first():
            await asyncio.sleep(0.2)
            proxy._current_client = None

        task = asyncio.create_task(disconnect_first())

        # Second client connects
        c_reader = asyncio.StreamReader()
        c_writer = MagicMock()
        c_writer.close = MagicMock()
        c_writer.wait_closed = AsyncMock()

        await asyncio.wait_for(proxy._handle_client(c_reader, c_writer), timeout=5)

        await task

        assert bridge_called, (
            "Second client should be served after first disconnects, not rejected. "
            "The proxy should queue incoming connections."
        )


# ── Bug 6 (P0): Proxy startup timeout UX ────────────────────────────────────


class TestProxyStartupFastFail:
    """_start_proxy should detect immediate crashes within ~0.5s instead of
    waiting 30 seconds for the state-file poll to time out."""

    @staticmethod
    def _make_dead_popen(log_msg, exit_code=1):
        """Create a fake Popen that simulates a process dying immediately.

        Writes log_msg to the log file (via the stderr kwarg) to simulate
        what the real subprocess would do before crashing.
        """
        dead_process = MagicMock()
        dead_process.poll.return_value = exit_code
        dead_process.returncode = exit_code

        def fake_popen(*args, **kwargs):
            # The real process would write to stderr (the log file fd).
            # Simulate that by writing directly to the file handle.
            stderr_file = kwargs.get("stderr")
            if stderr_file and hasattr(stderr_file, "write"):
                stderr_file.write(log_msg)
                stderr_file.flush()
            return dead_process

        return fake_popen, dead_process

    @pytest.mark.asyncio
    async def test_immediate_crash_raises_within_one_poll(self, tmp_path):
        """If the proxy process exits immediately, _start_proxy should raise
        RuntimeError with the exit code and log contents — NOT wait for the
        full polling timeout."""
        mgr = _make_manager()
        mgr.STATE_DIR = tmp_path

        fake_popen, _ = self._make_dead_popen(
            "ModuleNotFoundError: No module named 'foo'\n", exit_code=1
        )

        with (
            patch.object(LspServerManager, "STATE_DIR", tmp_path),
            patch("subprocess.Popen", side_effect=fake_popen),
            patch("asyncio.sleep", return_value=None),
            pytest.raises(RuntimeError, match="failed to start.*exit code 1"),
        ):
            await mgr._start_proxy(
                language="rust",
                workspace=Path("/tmp/ws"),
                server_config={"command": ["rust-analyzer"]},
                init_options={},
            )

    @pytest.mark.asyncio
    async def test_immediate_crash_includes_log_content(self, tmp_path):
        """The error message should include the log file content so the user
        (or LLM) can see what went wrong."""
        mgr = _make_manager()
        mgr.STATE_DIR = tmp_path

        fake_popen, _ = self._make_dead_popen(
            "ModuleNotFoundError: No module named 'foo'\n", exit_code=1
        )

        with (
            patch.object(LspServerManager, "STATE_DIR", tmp_path),
            patch("subprocess.Popen", side_effect=fake_popen),
            patch("asyncio.sleep", return_value=None),
            pytest.raises(RuntimeError, match="ModuleNotFoundError"),
        ):
            await mgr._start_proxy(
                language="rust",
                workspace=Path("/tmp/ws"),
                server_config={"command": ["rust-analyzer"]},
                init_options={},
            )

    @pytest.mark.asyncio
    async def test_crash_during_polling_detected(self, tmp_path):
        """If the process dies DURING the polling loop (not immediately), it
        should still be caught on the next poll iteration."""
        mgr = _make_manager()
        mgr.STATE_DIR = tmp_path

        # Process stays alive for initial check, alive on first poll, then dies
        process = MagicMock()
        process.poll.side_effect = [
            None,
            None,
            2,
        ]  # initial: alive, poll1: alive, poll2: dead
        process.returncode = 2

        def fake_popen(*args, **kwargs):
            stderr_file = kwargs.get("stderr")
            if stderr_file and hasattr(stderr_file, "write"):
                stderr_file.write("Segfault or something\n")
                stderr_file.flush()
            return process

        with (
            patch.object(LspServerManager, "STATE_DIR", tmp_path),
            patch("subprocess.Popen", side_effect=fake_popen),
            patch("asyncio.sleep", return_value=None),
            pytest.raises(RuntimeError, match="crashed during startup.*exit code 2"),
        ):
            await mgr._start_proxy(
                language="rust",
                workspace=Path("/tmp/ws"),
                server_config={"command": ["rust-analyzer"]},
                init_options={},
            )


class TestProxyStartupTimeout:
    """The polling timeout should be 15 seconds (30 * 0.5s), and error
    messages should include the log file path."""

    @pytest.mark.asyncio
    async def test_timeout_is_15_seconds(self, tmp_path):
        """The polling loop should iterate 30 times (30 * 0.5s = 15s),
        not 60 times (30s)."""
        mgr = _make_manager()
        mgr.STATE_DIR = tmp_path

        # Process stays alive but never writes state file
        alive_process = MagicMock()
        alive_process.poll.return_value = None  # always alive

        sleep_count = 0

        async def counting_sleep(duration):
            nonlocal sleep_count
            sleep_count += 1
            # Don't actually sleep

        with (
            patch.object(LspServerManager, "STATE_DIR", tmp_path),
            patch("subprocess.Popen", return_value=alive_process),
            patch("asyncio.sleep", side_effect=counting_sleep),
            pytest.raises(RuntimeError, match="15 seconds"),
        ):
            await mgr._start_proxy(
                language="rust",
                workspace=Path("/tmp/ws"),
                server_config={"command": ["rust-analyzer"]},
                init_options={},
            )

        # Should poll 30 times (30 * 0.5s = 15s), plus 1 initial sleep
        # The initial sleep is the 0.5s after Popen before the first poll check
        assert sleep_count == 31, (
            f"Expected 31 sleeps (1 initial + 30 poll), got {sleep_count}"
        )

    @pytest.mark.asyncio
    async def test_timeout_error_includes_log_path(self, tmp_path):
        """When the proxy times out, the error should mention the log path."""
        mgr = _make_manager()
        mgr.STATE_DIR = tmp_path

        alive_process = MagicMock()
        alive_process.poll.return_value = None

        with (
            patch.object(LspServerManager, "STATE_DIR", tmp_path),
            patch("subprocess.Popen", return_value=alive_process),
            patch("asyncio.sleep", return_value=None),
            pytest.raises(RuntimeError, match="Check log at"),
        ):
            await mgr._start_proxy(
                language="rust",
                workspace=Path("/tmp/ws"),
                server_config={"command": ["rust-analyzer"]},
                init_options={},
            )
