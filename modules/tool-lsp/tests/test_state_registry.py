# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false
"""Tests for Task 2: State File Registry in LspServerManager.

Tests the reading side of state file management — discovery, validation,
and cleanup of proxy state files written by proxy.py.
"""

import hashlib
import json
import os
import socket
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from amplifier_module_tool_lsp.server import LspServerManager


# ── State key computation ────────────────────────────────────────────────────


class TestComputeStateKey:
    """_compute_state_key should produce deterministic keys from workspace paths."""

    def test_deterministic(self, tmp_path):
        """Same workspace always produces the same key."""
        key1 = LspServerManager._compute_state_key("rust", tmp_path)
        key2 = LspServerManager._compute_state_key("rust", tmp_path)
        assert key1 == key2

    def test_different_workspaces_produce_different_keys(self, tmp_path):
        """Different workspace paths produce different keys."""
        ws_a = tmp_path / "project-a"
        ws_b = tmp_path / "project-b"
        ws_a.mkdir()
        ws_b.mkdir()
        key_a = LspServerManager._compute_state_key("rust", ws_a)
        key_b = LspServerManager._compute_state_key("rust", ws_b)
        assert key_a != key_b

    def test_key_format(self, tmp_path):
        """Key should be {language}-{8 hex chars}."""
        key = LspServerManager._compute_state_key("rust", tmp_path)
        assert key.startswith("rust-")
        hash_part = key.split("-", 1)[1]
        assert len(hash_part) == 8
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_uses_realpath(self, tmp_path):
        """Key should resolve symlinks via os.path.realpath."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)

        key_real = LspServerManager._compute_state_key("rust", real_dir)
        key_link = LspServerManager._compute_state_key("rust", link_dir)
        assert key_real == key_link

    def test_matches_proxy_computation(self, tmp_path):
        """Key must match the computation in proxy.py exactly."""
        workspace = tmp_path / "project"
        workspace.mkdir()
        canonical = os.path.realpath(str(workspace))
        expected_hash = hashlib.sha256(canonical.encode()).hexdigest()[:8]
        expected_key = f"rust-{expected_hash}"

        key = LspServerManager._compute_state_key("rust", workspace)
        assert key == expected_key


# ── State file path ──────────────────────────────────────────────────────────


class TestStateFilePath:
    """_state_file_path should return correct path under STATE_DIR."""

    def test_correct_directory(self, tmp_path):
        """State file should be under STATE_DIR."""
        path = LspServerManager._state_file_path("rust", tmp_path)
        assert path.parent == LspServerManager.STATE_DIR

    def test_correct_filename(self, tmp_path):
        """State file should be named {key}.json."""
        key = LspServerManager._compute_state_key("rust", tmp_path)
        path = LspServerManager._state_file_path("rust", tmp_path)
        assert path.name == f"{key}.json"


# ── PID liveness ─────────────────────────────────────────────────────────────


class TestIsPidAlive:
    """_is_pid_alive should detect running vs dead processes."""

    def test_current_pid_is_alive(self):
        """Our own PID should be alive."""
        assert LspServerManager._is_pid_alive(os.getpid()) is True

    def test_nonexistent_pid_is_not_alive(self):
        """A PID that doesn't exist should not be alive."""
        assert LspServerManager._is_pid_alive(999999999) is False


# ── Port connectivity ────────────────────────────────────────────────────────


class TestIsPortConnectable:
    """_is_port_connectable should detect listening vs closed ports."""

    def test_listening_port_is_connectable(self):
        """A port with a TCP listener should be connectable."""
        # Start a real TCP listener on an ephemeral port
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]

        try:
            assert LspServerManager._is_port_connectable(port) is True
        finally:
            server_sock.close()

    def test_non_listening_port_is_not_connectable(self):
        """A port with no listener should not be connectable."""
        # Find a port that's definitely not in use
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        # Port is now closed — no listener
        assert LspServerManager._is_port_connectable(port, timeout=0.5) is False


# ── Read state ───────────────────────────────────────────────────────────────


class TestReadState:
    """_read_state should validate state files and clean up stale ones."""

    def _make_manager(self, state_dir):
        """Create a manager with a custom STATE_DIR."""
        mgr = LspServerManager.__new__(LspServerManager)
        mgr._servers = {}
        mgr._timeout = 30.0
        mgr._on_shutdown = None
        # Patch STATE_DIR at instance level won't work for static methods,
        # so we patch the class constant
        return mgr

    def test_missing_file_returns_none(self, tmp_path):
        """If state file doesn't exist, return None."""
        mgr = self._make_manager(tmp_path)
        with patch.object(LspServerManager, "STATE_DIR", tmp_path):
            result = mgr._read_state("rust", tmp_path / "nonexistent-workspace")
        assert result is None

    def test_corrupt_json_returns_none_and_removes(self, tmp_path):
        """Corrupt JSON state file should return None and be removed."""
        mgr = self._make_manager(tmp_path)
        with patch.object(LspServerManager, "STATE_DIR", tmp_path):
            # Write a corrupt file at the expected path
            state_path = LspServerManager._state_file_path("rust", tmp_path / "ws")
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text("NOT VALID JSON {{{")

            result = mgr._read_state("rust", tmp_path / "ws")
            assert result is None
            assert not state_path.exists()

    def test_dead_pid_returns_none_and_removes(self, tmp_path):
        """State file with dead proxy_pid should return None and be removed."""
        mgr = self._make_manager(tmp_path)
        with patch.object(LspServerManager, "STATE_DIR", tmp_path):
            state_path = LspServerManager._state_file_path("rust", tmp_path / "ws")
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "language": "rust",
                "workspace_root": str(tmp_path / "ws"),
                "port": 12345,
                "proxy_pid": 999999999,  # Dead PID
            }
            state_path.write_text(json.dumps(state))

            result = mgr._read_state("rust", tmp_path / "ws")
            assert result is None
            assert not state_path.exists()

    def test_alive_pid_unreachable_port_returns_none(self, tmp_path):
        """State with alive PID but unreachable port should return None."""
        mgr = self._make_manager(tmp_path)
        with patch.object(LspServerManager, "STATE_DIR", tmp_path):
            # Find a port that's not in use
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()

            state_path = LspServerManager._state_file_path("rust", tmp_path / "ws")
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "language": "rust",
                "workspace_root": str(tmp_path / "ws"),
                "port": port,
                "proxy_pid": os.getpid(),  # Alive PID (us)
            }
            state_path.write_text(json.dumps(state))

            result = mgr._read_state("rust", tmp_path / "ws")
            assert result is None
            assert not state_path.exists()

    def test_valid_state_returned(self, tmp_path):
        """Valid state file (alive PID + connectable port) should return dict."""
        mgr = self._make_manager(tmp_path)

        # Start a real TCP listener
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]

        # Accept connections in a background thread to prevent blocking
        def accept_loop():
            try:
                conn, _ = server_sock.accept()
                conn.close()
            except OSError:
                pass

        accept_thread = threading.Thread(target=accept_loop, daemon=True)
        accept_thread.start()

        try:
            with patch.object(LspServerManager, "STATE_DIR", tmp_path):
                state_path = LspServerManager._state_file_path("rust", tmp_path / "ws")
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state = {
                    "language": "rust",
                    "workspace_root": str(tmp_path / "ws"),
                    "port": port,
                    "proxy_pid": os.getpid(),
                }
                state_path.write_text(json.dumps(state))

                result = mgr._read_state("rust", tmp_path / "ws")
                assert result is not None
                assert result["port"] == port
                assert result["proxy_pid"] == os.getpid()
        finally:
            server_sock.close()
            accept_thread.join(timeout=2)


# ── Remove state file ────────────────────────────────────────────────────────


class TestRemoveStateFile:
    """_remove_state_file should handle present and missing files gracefully."""

    def test_removes_existing_file(self, tmp_path):
        """Should remove a file that exists."""
        f = tmp_path / "test.json"
        f.write_text("{}")
        LspServerManager._remove_state_file(f)
        assert not f.exists()

    def test_missing_file_no_error(self, tmp_path):
        """Should not raise when file doesn't exist."""
        f = tmp_path / "nonexistent.json"
        LspServerManager._remove_state_file(f)  # Should not raise


# ── Cleanup stale states ─────────────────────────────────────────────────────


class TestCleanupStaleStates:
    """_cleanup_stale_states should remove state files with dead PIDs."""

    def test_removes_dead_pid_state_files(self, tmp_path):
        """State files with dead proxy_pid should be removed."""
        mgr = LspServerManager.__new__(LspServerManager)
        mgr._servers = {}
        mgr._timeout = 30.0
        mgr._on_shutdown = None

        with patch.object(LspServerManager, "STATE_DIR", tmp_path):
            # Create a state file with a dead PID
            state_file = tmp_path / "rust-deadbeef.json"
            state_file.write_text(json.dumps({"proxy_pid": 999999999, "port": 12345}))

            mgr._cleanup_stale_states()
            assert not state_file.exists()

    def test_keeps_alive_pid_state_files(self, tmp_path):
        """State files with alive proxy_pid should be kept."""
        mgr = LspServerManager.__new__(LspServerManager)
        mgr._servers = {}
        mgr._timeout = 30.0
        mgr._on_shutdown = None

        with patch.object(LspServerManager, "STATE_DIR", tmp_path):
            state_file = tmp_path / "rust-alivepid.json"
            state_file.write_text(json.dumps({"proxy_pid": os.getpid(), "port": 12345}))

            mgr._cleanup_stale_states()
            assert state_file.exists()

    def test_removes_corrupt_state_files(self, tmp_path):
        """Corrupt JSON state files should be removed."""
        mgr = LspServerManager.__new__(LspServerManager)
        mgr._servers = {}
        mgr._timeout = 30.0
        mgr._on_shutdown = None

        with patch.object(LspServerManager, "STATE_DIR", tmp_path):
            state_file = tmp_path / "rust-corrupt1.json"
            state_file.write_text("NOT JSON")

            mgr._cleanup_stale_states()
            assert not state_file.exists()

    def test_nonexistent_state_dir_no_error(self):
        """If STATE_DIR doesn't exist, should not raise."""
        mgr = LspServerManager.__new__(LspServerManager)
        mgr._servers = {}
        mgr._timeout = 30.0
        mgr._on_shutdown = None

        with patch.object(
            LspServerManager, "STATE_DIR", Path("/tmp/nonexistent-dir-12345")
        ):
            mgr._cleanup_stale_states()  # Should not raise

    def test_cleanup_called_from_init(self, tmp_path):
        """LspServerManager.__init__() should call _cleanup_stale_states."""
        with patch.object(LspServerManager, "_cleanup_stale_states") as mock_cleanup:
            LspServerManager()
            mock_cleanup.assert_called_once()


# ── Start proxy ──────────────────────────────────────────────────────────────


class TestStartProxy:
    """_start_proxy should launch a detached proxy and wait for readiness."""

    @pytest.mark.asyncio
    async def test_start_proxy_launches_subprocess(self, tmp_path):
        """_start_proxy should call subprocess.Popen with correct args."""
        mgr = LspServerManager.__new__(LspServerManager)
        mgr._servers = {}
        mgr._timeout = 30.0
        mgr._on_shutdown = None

        workspace = tmp_path / "ws"
        workspace.mkdir()

        # We'll mock Popen and have the state file appear "magically"
        # to simulate the proxy writing it
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]

        # Accept connections in background
        def accept_loop():
            try:
                while True:
                    conn, _ = server_sock.accept()
                    conn.close()
            except OSError:
                pass

        accept_thread = threading.Thread(target=accept_loop, daemon=True)
        accept_thread.start()

        popen_called_with = {}

        import subprocess as sp

        def fake_popen(args, **kwargs):
            popen_called_with["args"] = args
            popen_called_with["kwargs"] = kwargs
            # Simulate proxy writing state file
            with patch.object(LspServerManager, "STATE_DIR", tmp_path):
                state_path = LspServerManager._state_file_path("rust", workspace)
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state = {
                    "language": "rust",
                    "workspace_root": str(workspace),
                    "port": port,
                    "proxy_pid": os.getpid(),
                }
                state_path.write_text(json.dumps(state))

        try:
            with (
                patch.object(LspServerManager, "STATE_DIR", tmp_path),
                patch("subprocess.Popen", side_effect=fake_popen),
            ):
                state = await mgr._start_proxy(
                    language="rust",
                    workspace=workspace,
                    server_config={"command": ["rust-analyzer"]},
                    init_options={},
                    idle_timeout=300,
                )

            assert state is not None
            assert state["port"] == port
            # Verify Popen was called with detach flags
            assert popen_called_with["kwargs"]["start_new_session"] is True
            assert popen_called_with["kwargs"]["stdin"] == sp.DEVNULL
            assert popen_called_with["kwargs"]["stdout"] == sp.DEVNULL
            # stderr goes to a log file (not DEVNULL) for diagnosable failures
            assert popen_called_with["kwargs"]["stderr"] != sp.DEVNULL
            # Verify args contain the proxy module
            args = popen_called_with["args"]
            assert "-m" in args
            assert "amplifier_module_tool_lsp.proxy" in args
            assert "--language" in args
            assert "rust" in args
        finally:
            server_sock.close()
            accept_thread.join(timeout=2)

    @pytest.mark.asyncio
    async def test_start_proxy_timeout(self, tmp_path):
        """If proxy never writes state file, should raise RuntimeError."""
        mgr = LspServerManager.__new__(LspServerManager)
        mgr._servers = {}
        mgr._timeout = 30.0
        mgr._on_shutdown = None

        workspace = tmp_path / "ws"
        workspace.mkdir()

        # Mock Popen to do nothing (proxy never starts)
        with (
            patch.object(LspServerManager, "STATE_DIR", tmp_path),
            patch("subprocess.Popen"),
            patch("asyncio.sleep", return_value=None),  # Speed up polling
            pytest.raises(RuntimeError, match="failed to start"),
        ):
            await mgr._start_proxy(
                language="rust",
                workspace=workspace,
                server_config={"command": ["rust-analyzer"]},
                init_options={},
                idle_timeout=300,
            )
