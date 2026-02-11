# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportArgumentType=false
"""Tests for Task 4: Lifecycle Config + LspServerManager Integration.

Tests the routing logic in get_server() that directs to either:
- _get_direct_server() for session lifecycle (default)
- _get_or_create_proxy_server() for persistent/timeout lifecycle
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_tool_lsp.server import LspServerManager, LspServer, ProxyLspServer


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_manager():
    """Create an LspServerManager without triggering __init__ side effects."""
    mgr = LspServerManager.__new__(LspServerManager)
    mgr._servers = {}
    mgr._timeout = 30.0
    mgr._on_shutdown = None
    return mgr


def _make_server_config(lifecycle=None, **extras):
    """Build a minimal server_config dict."""
    config = {"command": ["fake-server"], **extras}
    if lifecycle is not None:
        config["lifecycle"] = lifecycle
    return config


def _make_fake_lsp_server():
    """Create a mock LspServer instance."""
    server = MagicMock(spec=LspServer)
    server.shutdown = AsyncMock()
    return server


def _make_fake_proxy_server(port=12345):
    """Create a mock ProxyLspServer instance."""
    server = MagicMock(spec=ProxyLspServer)
    server._port = port
    server.shutdown = AsyncMock()
    return server


# ── Lifecycle routing in get_server() ────────────────────────────────────────


class TestLifecycleRouting:
    """get_server() should route based on server_config['lifecycle']."""

    @pytest.mark.asyncio
    async def test_session_mode_uses_direct_server(self):
        """lifecycle='session' should call _get_direct_server."""
        mgr = _make_manager()
        fake_server = _make_fake_lsp_server()

        mgr._get_direct_server = AsyncMock(return_value=fake_server)
        mgr._get_or_create_proxy_server = AsyncMock()

        config = _make_server_config(lifecycle="session")
        result = await mgr.get_server("python", Path("/tmp/ws"), config, {})

        mgr._get_direct_server.assert_called_once_with(
            "python", Path("/tmp/ws"), config, {}
        )
        mgr._get_or_create_proxy_server.assert_not_called()
        assert result is fake_server

    @pytest.mark.asyncio
    async def test_timeout_mode_uses_proxy_server(self):
        """lifecycle='timeout' should call _get_or_create_proxy_server."""
        mgr = _make_manager()
        fake_proxy = _make_fake_proxy_server()

        mgr._get_direct_server = AsyncMock()
        mgr._get_or_create_proxy_server = AsyncMock(return_value=fake_proxy)

        config = _make_server_config(lifecycle="timeout")
        result = await mgr.get_server("rust", Path("/tmp/ws"), config, {})

        mgr._get_or_create_proxy_server.assert_called_once_with(
            "rust", Path("/tmp/ws"), config, {}
        )
        mgr._get_direct_server.assert_not_called()
        assert result is fake_proxy

    @pytest.mark.asyncio
    async def test_persistent_mode_uses_proxy_server(self):
        """lifecycle='persistent' should call _get_or_create_proxy_server."""
        mgr = _make_manager()
        fake_proxy = _make_fake_proxy_server()

        mgr._get_direct_server = AsyncMock()
        mgr._get_or_create_proxy_server = AsyncMock(return_value=fake_proxy)

        config = _make_server_config(lifecycle="persistent")
        result = await mgr.get_server("rust", Path("/tmp/ws"), config, {})

        mgr._get_or_create_proxy_server.assert_called_once_with(
            "rust", Path("/tmp/ws"), config, {}
        )
        mgr._get_direct_server.assert_not_called()
        assert result is fake_proxy

    @pytest.mark.asyncio
    async def test_default_lifecycle_is_session(self):
        """No lifecycle key in config should default to session mode."""
        mgr = _make_manager()
        fake_server = _make_fake_lsp_server()

        mgr._get_direct_server = AsyncMock(return_value=fake_server)
        mgr._get_or_create_proxy_server = AsyncMock()

        config = _make_server_config()  # No lifecycle key
        result = await mgr.get_server("python", Path("/tmp/ws"), config, {})

        mgr._get_direct_server.assert_called_once()
        mgr._get_or_create_proxy_server.assert_not_called()
        assert result is fake_server


# ── Cache behavior ───────────────────────────────────────────────────────────


class TestServerCache:
    """get_server() should cache and return cached servers."""

    @pytest.mark.asyncio
    async def test_cached_server_returned(self):
        """Second call with same key should return cached server, no new creation."""
        mgr = _make_manager()
        fake_server = _make_fake_lsp_server()

        mgr._get_direct_server = AsyncMock(return_value=fake_server)

        config = _make_server_config(lifecycle="session")
        ws = Path("/tmp/ws")

        # First call creates
        result1 = await mgr.get_server("python", ws, config, {})
        # Second call should use cache
        result2 = await mgr.get_server("python", ws, config, {})

        assert result1 is result2
        # _get_direct_server should only be called once
        assert mgr._get_direct_server.call_count == 1

    @pytest.mark.asyncio
    async def test_stale_proxy_reconnects(self):
        """Cached ProxyLspServer with dead port should be evicted and reconnected."""
        mgr = _make_manager()

        # Pre-populate cache with a proxy server
        stale_proxy = _make_fake_proxy_server(port=11111)
        key = mgr._server_key("rust", Path("/tmp/ws"))
        mgr._servers[key] = stale_proxy

        # Port is NOT connectable (stale)
        fresh_proxy = _make_fake_proxy_server(port=22222)
        mgr._get_or_create_proxy_server = AsyncMock(return_value=fresh_proxy)

        config = _make_server_config(lifecycle="persistent")

        with patch.object(LspServerManager, "_is_port_connectable", return_value=False):
            result = await mgr.get_server("rust", Path("/tmp/ws"), config, {})

        # Should have evicted stale and created new
        assert result is fresh_proxy
        mgr._get_or_create_proxy_server.assert_called_once()

    @pytest.mark.asyncio
    async def test_cached_proxy_returned_when_alive(self):
        """Cached ProxyLspServer with alive port should be returned directly."""
        mgr = _make_manager()

        cached_proxy = _make_fake_proxy_server(port=12345)
        key = mgr._server_key("rust", Path("/tmp/ws"))
        mgr._servers[key] = cached_proxy

        config = _make_server_config(lifecycle="persistent")

        with patch.object(LspServerManager, "_is_port_connectable", return_value=True):
            result = await mgr.get_server("rust", Path("/tmp/ws"), config, {})

        assert result is cached_proxy


# ── _get_direct_server ───────────────────────────────────────────────────────


class TestGetDirectServer:
    """_get_direct_server should validate install and create LspServer."""

    @pytest.mark.asyncio
    async def test_get_direct_server_validates_install(self):
        """_get_direct_server should call _validate_server_installation when install_check present."""
        mgr = _make_manager()
        fake_server = _make_fake_lsp_server()

        config = _make_server_config(
            install_check=["fake-server", "--version"],
            install_hint="pip install fake-server",
        )

        with (
            patch.object(
                LspServerManager, "_validate_server_installation"
            ) as mock_validate,
            patch.object(
                LspServer, "create", new_callable=AsyncMock, return_value=fake_server
            ),
        ):
            result = await mgr._get_direct_server("python", Path("/tmp/ws"), config, {})

        mock_validate.assert_called_once_with(
            language="python",
            install_check=["fake-server", "--version"],
            server_config=config,
        )
        assert result is fake_server

    @pytest.mark.asyncio
    async def test_get_direct_server_skips_validation_when_no_install_check(self):
        """_get_direct_server without install_check should skip validation."""
        mgr = _make_manager()
        fake_server = _make_fake_lsp_server()

        config = _make_server_config()  # No install_check

        with (
            patch.object(
                LspServerManager, "_validate_server_installation"
            ) as mock_validate,
            patch.object(
                LspServer, "create", new_callable=AsyncMock, return_value=fake_server
            ),
        ):
            result = await mgr._get_direct_server("python", Path("/tmp/ws"), config, {})

        mock_validate.assert_not_called()
        assert result is fake_server

    @pytest.mark.asyncio
    async def test_get_direct_server_passes_correct_args_to_create(self):
        """_get_direct_server should pass language, workspace, command, init_options, timeout."""
        mgr = _make_manager()
        mgr._timeout = 45.0
        fake_server = _make_fake_lsp_server()

        config = _make_server_config()

        with patch.object(
            LspServer, "create", new_callable=AsyncMock, return_value=fake_server
        ) as mock_create:
            await mgr._get_direct_server(
                "python", Path("/tmp/ws"), config, {"setting": "val"}
            )

        mock_create.assert_called_once_with(
            language="python",
            workspace=Path("/tmp/ws"),
            command=["fake-server"],
            init_options={"setting": "val"},
            timeout=45.0,
        )


# ── _get_or_create_proxy_server ──────────────────────────────────────────────


class TestGetOrCreateProxyServer:
    """_get_or_create_proxy_server should find existing proxy or start new one."""

    @pytest.mark.asyncio
    async def test_get_or_create_finds_existing_proxy(self):
        """With valid state file, should connect to existing proxy without starting new one."""
        mgr = _make_manager()
        fake_proxy = _make_fake_proxy_server()

        config = _make_server_config(lifecycle="persistent")

        with (
            patch.object(
                mgr, "_read_state", return_value={"port": 12345, "proxy_pid": 999}
            ),
            patch.object(
                ProxyLspServer,
                "connect",
                new_callable=AsyncMock,
                return_value=fake_proxy,
            ) as mock_connect,
            patch.object(mgr, "_start_proxy", new_callable=AsyncMock) as mock_start,
        ):
            result = await mgr._get_or_create_proxy_server(
                "rust", Path("/tmp/ws"), config, {}
            )

        mock_connect.assert_called_once_with(
            language="rust",
            workspace=Path("/tmp/ws"),
            port=12345,
            init_options={},
            timeout=mgr._timeout,
        )
        mock_start.assert_not_called()
        assert result is fake_proxy

    @pytest.mark.asyncio
    async def test_get_or_create_starts_new_proxy(self):
        """No state file → should start new proxy and connect."""
        mgr = _make_manager()
        fake_proxy = _make_fake_proxy_server()

        config = _make_server_config(lifecycle="timeout", idle_timeout=600)

        with (
            patch.object(mgr, "_read_state", return_value=None),
            patch.object(
                mgr,
                "_start_proxy",
                new_callable=AsyncMock,
                return_value={"port": 54321, "proxy_pid": 1234},
            ),
            patch.object(
                ProxyLspServer,
                "connect",
                new_callable=AsyncMock,
                return_value=fake_proxy,
            ) as mock_connect,
            patch.object(LspServerManager, "_validate_server_installation"),
        ):
            result = await mgr._get_or_create_proxy_server(
                "rust", Path("/tmp/ws"), config, {"key": "val"}
            )

        mock_connect.assert_called_once_with(
            language="rust",
            workspace=Path("/tmp/ws"),
            port=54321,
            init_options={"key": "val"},
            timeout=mgr._timeout,
        )
        assert result is fake_proxy

    @pytest.mark.asyncio
    async def test_get_or_create_cleans_stale_and_starts_new(self):
        """State file exists but connection fails → clean state, start new proxy."""
        mgr = _make_manager()
        fake_proxy = _make_fake_proxy_server()

        config = _make_server_config(lifecycle="persistent")

        # First connect attempt fails (stale state), second succeeds (new proxy)
        connect_calls = [0]

        async def mock_connect(**kwargs):
            connect_calls[0] += 1
            if connect_calls[0] == 1:
                raise ConnectionRefusedError("stale proxy")
            return fake_proxy

        with (
            patch.object(
                mgr, "_read_state", return_value={"port": 11111, "proxy_pid": 999}
            ),
            patch.object(
                mgr, "_state_file_path", return_value=Path("/tmp/fake-state.json")
            ),
            patch.object(LspServerManager, "_remove_state_file") as mock_remove,
            patch.object(
                mgr,
                "_start_proxy",
                new_callable=AsyncMock,
                return_value={"port": 22222, "proxy_pid": 1234},
            ),
            patch.object(ProxyLspServer, "connect", side_effect=mock_connect),
            patch.object(LspServerManager, "_validate_server_installation"),
        ):
            result = await mgr._get_or_create_proxy_server(
                "rust", Path("/tmp/ws"), config, {}
            )

        # Should have cleaned up stale state file
        mock_remove.assert_called_once()
        # Should have connected twice (once failed, once succeeded)
        assert connect_calls[0] == 2
        assert result is fake_proxy

    @pytest.mark.asyncio
    async def test_get_or_create_validates_install_before_starting(self):
        """When starting a new proxy, should validate install first."""
        mgr = _make_manager()
        fake_proxy = _make_fake_proxy_server()

        config = _make_server_config(
            lifecycle="timeout",
            install_check=["rust-analyzer", "--version"],
            install_hint="brew install rust-analyzer",
        )

        with (
            patch.object(mgr, "_read_state", return_value=None),
            patch.object(
                LspServerManager, "_validate_server_installation"
            ) as mock_validate,
            patch.object(
                mgr,
                "_start_proxy",
                new_callable=AsyncMock,
                return_value={"port": 54321, "proxy_pid": 1234},
            ),
            patch.object(
                ProxyLspServer,
                "connect",
                new_callable=AsyncMock,
                return_value=fake_proxy,
            ),
        ):
            await mgr._get_or_create_proxy_server("rust", Path("/tmp/ws"), config, {})

        mock_validate.assert_called_once_with(
            language="rust",
            install_check=["rust-analyzer", "--version"],
            server_config=config,
        )

    @pytest.mark.asyncio
    async def test_get_or_create_uses_default_idle_timeout(self):
        """idle_timeout should default to 300 when not specified."""
        mgr = _make_manager()
        fake_proxy = _make_fake_proxy_server()

        config = _make_server_config(lifecycle="timeout")  # No idle_timeout

        with (
            patch.object(mgr, "_read_state", return_value=None),
            patch.object(
                mgr,
                "_start_proxy",
                new_callable=AsyncMock,
                return_value={"port": 54321, "proxy_pid": 1234},
            ) as mock_start,
            patch.object(
                ProxyLspServer,
                "connect",
                new_callable=AsyncMock,
                return_value=fake_proxy,
            ),
        ):
            await mgr._get_or_create_proxy_server("rust", Path("/tmp/ws"), config, {})

        mock_start.assert_called_once_with(
            language="rust",
            workspace=Path("/tmp/ws"),
            server_config=config,
            init_options={},
            idle_timeout=300,
        )
