"""LSP Server Management - handles server lifecycle.

Uses structured concurrency pattern for subprocess cleanup to prevent
"Event loop is closed" errors. See: https://github.com/python/cpython/issues/114177

The key insight: always await process.wait() (or communicate()) to ensure the
event loop registers process termination before returning.
"""

import asyncio
import contextlib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class LspServer:
    """Wrapper around an LSP server process."""

    def __init__(
        self,
        language: str,
        workspace: Path,
        process: asyncio.subprocess.Process,
        timeout: float = 30.0,
    ):
        self.language = language
        self.workspace = workspace
        self._process = process
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._default_timeout = timeout
        self._diagnostics_cache: dict[str, list] = {}

    @classmethod
    async def create(
        cls,
        language: str,
        workspace: Path,
        command: list[str],
        init_options: dict[str, Any],
        timeout: float = 30.0,
    ) -> "LspServer":
        """Create and initialize an LSP server."""
        # Start the server process
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
        )

        server = cls(language, workspace, process, timeout=timeout)
        server._reader_task = asyncio.create_task(server._read_responses())

        # Initialize the server
        await server._initialize(init_options)

        # Trigger workspace indexing to reduce cold-start latency for first operations
        # workspace/symbol with empty query primes the index without blocking
        try:
            await asyncio.wait_for(
                server.request("workspace/symbol", {"query": ""}),
                timeout=5.0,
            )
        except Exception:
            pass  # Indexing not complete yet, that's OK — operations will still work

        return server

    async def _initialize(self, init_options: dict[str, Any]):
        """Send initialize request to LSP server."""
        result = await self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": self.workspace.as_uri(),
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": True},
                        "references": {"dynamicRegistration": True},
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "documentSymbol": {
                            "dynamicRegistration": True,
                            "hierarchicalDocumentSymbolSupport": True,
                        },
                        "implementation": {"dynamicRegistration": True},
                        "callHierarchy": {"dynamicRegistration": True},
                        "typeHierarchy": {"dynamicRegistration": True},
                        "diagnostic": {"dynamicRegistration": True},
                        "rename": {
                            "dynamicRegistration": True,
                            "prepareSupport": True,
                        },
                        "codeAction": {
                            "dynamicRegistration": True,
                            "codeActionLiteralSupport": {
                                "codeActionKind": {
                                    "valueSet": [
                                        "quickfix",
                                        "refactor",
                                        "refactor.extract",
                                        "refactor.inline",
                                        "refactor.rewrite",
                                        "source",
                                        "source.organizeImports",
                                    ]
                                }
                            },
                            "resolveSupport": {"properties": ["edit"]},
                        },
                        "inlayHint": {"dynamicRegistration": True},
                        "publishDiagnostics": {
                            "relatedInformation": True,
                            "tagSupport": {"valueSet": [1, 2]},
                        },
                    },
                    "workspace": {
                        "symbol": {"dynamicRegistration": True},
                        "workDoneProgress": True,
                    },
                    "experimental": {
                        "snippetTextEdit": True,
                        "codeActionGroup": True,
                        "hoverActions": True,
                        "serverStatusNotification": True,
                        "colorDiagnosticOutput": True,
                        "commands": {
                            "commands": [
                                "rust-analyzer.runSingle",
                                "rust-analyzer.showReferences",
                            ]
                        },
                    },
                },
                "initializationOptions": init_options,
            },
        )

        # Send initialized notification
        await self.notify("initialized", {})

        return result

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        """Send a request and wait for response."""
        self._request_id += 1
        request_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        await self._send(message)

        return await asyncio.wait_for(future, timeout=self._default_timeout)

    async def notify(self, method: str, params: dict[str, Any]):
        """Send a notification (no response expected)."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._send(message)

    async def _send(self, message: dict):
        """Send a JSON-RPC message to the server."""
        assert self._process.stdin is not None, "Process stdin not available"
        content = json.dumps(message)
        header = f"Content-Length: {len(content)}\r\n\r\n"
        self._process.stdin.write((header + content).encode())
        await self._process.stdin.drain()

    async def _read_responses(self):
        """Background task to read responses from server."""
        assert self._process.stdout is not None, "Process stdout not available"
        while True:
            try:
                # Read header
                header = await self._process.stdout.readline()
                if not header:
                    break

                # Parse content length
                if header.startswith(b"Content-Length:"):
                    content_length = int(header.split(b":")[1].strip())

                    # Read blank line
                    await self._process.stdout.readline()

                    # Read content
                    content = await self._process.stdout.read(content_length)
                    message = json.loads(content)

                    if "id" in message and message["id"] in self._pending:
                        # Request/response correlation
                        future = self._pending.pop(message["id"])
                        if "error" in message:
                            err = message["error"]
                            err_msg = (
                                err.get("message")
                                or f"LSP error code {err.get('code', 'unknown')}"
                            )
                            if err.get("code") == -32601:
                                err_msg = f"Method not supported by server: {err_msg}"
                            future.set_exception(Exception(err_msg))
                        else:
                            future.set_result(message.get("result"))
                    elif "method" in message and "id" in message:
                        # Server-initiated request (has both method and id)
                        await self._handle_server_request(message)
                    elif "method" in message:
                        # Server notification (method but no id)
                        await self._handle_notification(
                            message["method"], message.get("params", {})
                        )
                    # else: unrecognized message, drop

            except asyncio.CancelledError:
                break
            except Exception:
                continue

    async def _handle_server_request(self, message: dict):
        """Respond to server-initiated requests."""
        method = message["method"]
        request_id = message["id"]

        if method == "client/registerCapability":
            await self._send_response(request_id, result=None)
        elif method == "workspace/configuration":
            items = message.get("params", {}).get("items", [])
            await self._send_response(request_id, result=[{} for _ in items])
        elif method == "window/workDoneProgress/create":
            await self._send_response(request_id, result=None)
        else:
            await self._send_response(request_id, result=None)

    async def _handle_notification(self, method: str, params: dict):
        """Handle server notifications."""
        if method == "textDocument/publishDiagnostics":
            uri = params.get("uri", "")
            self._diagnostics_cache[uri] = params.get("diagnostics", [])

    def get_cached_diagnostics(self, uri: str) -> list | None:
        """Return cached diagnostics for a URI, or None if not cached."""
        return self._diagnostics_cache.get(uri)

    async def _send_response(
        self, request_id: Any, result: Any = None, error: Any = None
    ):
        """Send a JSON-RPC response to a server-initiated request."""
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error:
            response["error"] = error
        else:
            response["result"] = result
        await self._send(response)

    async def shutdown(self):
        """Gracefully shutdown the server using structured concurrency.

        Key insight from CPython #114177: always await process completion to ensure
        the event loop registers process termination before returning. communicate()
        handles draining pipes and waiting properly.
        """
        try:
            await self.request("shutdown", {})
            await self.notify("exit", {})
        except Exception:
            pass
        finally:
            # Cancel reader task first
            if self._reader_task:
                self._reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reader_task

            # Close stdin to signal we're done writing
            if self._process.stdin:
                self._process.stdin.close()

            # Kill if still running, then use communicate() for structured concurrency.
            # communicate() drains pipes and awaits process exit, which is the key
            # to preventing "Event loop is closed" errors on Python <3.13.
            if self._process.returncode is None:
                self._process.kill()

            try:
                await asyncio.wait_for(self._process.communicate(), timeout=5.0)
            except (TimeoutError, Exception):
                # Fallback: ensure we still wait for process exit
                with contextlib.suppress(Exception):
                    await self._process.wait()


class LspServerManager:
    """Manages LSP server instances per workspace."""

    STATE_DIR = Path.home() / ".amplifier" / "lsp-servers"

    def __init__(self, timeout: float = 30.0, on_shutdown: Any = None):
        self._servers: dict[str, LspServer] = {}
        self._timeout = timeout
        self._on_shutdown = on_shutdown
        self._cleanup_stale_states()

    # ── State file registry ──────────────────────────────────────────────

    @staticmethod
    def _compute_state_key(language: str, workspace: Path) -> str:
        """Compute state file key: {language}-{hash8}."""
        canonical = os.path.realpath(str(workspace))
        hash8 = hashlib.sha256(canonical.encode()).hexdigest()[:8]
        return f"{language}-{hash8}"

    @staticmethod
    def _state_file_path(language: str, workspace: Path) -> Path:
        """Get the full path to a state file."""
        key = LspServerManager._compute_state_key(language, workspace)
        return LspServerManager.STATE_DIR / f"{key}.json"

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """Check if a process with the given PID is still running."""
        try:
            os.kill(pid, 0)  # Signal 0 = check existence, don't actually signal
            return True
        except (OSError, ProcessLookupError):
            return False

    @staticmethod
    def _is_port_connectable(port: int, timeout: float = 2.0) -> bool:
        """Check if a TCP port is accepting connections on localhost."""
        import socket

        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except (ConnectionRefusedError, TimeoutError, OSError):
            return False

    @staticmethod
    def _remove_state_file(state_path: Path) -> None:
        """Remove a state file, ignoring errors."""
        try:
            state_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _read_state(self, language: str, workspace: Path) -> dict | None:
        """Read and validate a proxy state file.

        Returns the state dict if the proxy is alive and reachable, None otherwise.
        Cleans up stale state files automatically.
        """
        state_path = self._state_file_path(language, workspace)
        if not state_path.exists():
            return None

        try:
            state = json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable state file — clean up
            self._remove_state_file(state_path)
            return None

        # Validate PID is alive
        proxy_pid = state.get("proxy_pid")
        if not proxy_pid or not self._is_pid_alive(proxy_pid):
            self._remove_state_file(state_path)
            return None

        # Validate TCP port is connectable
        port = state.get("port")
        if not port or not self._is_port_connectable(port):
            self._remove_state_file(state_path)
            return None

        return state

    def _cleanup_stale_states(self) -> None:
        """Remove state files for dead proxies. Called on startup."""
        if not self.STATE_DIR.exists():
            return

        for state_file in self.STATE_DIR.glob("*.json"):
            try:
                state = json.loads(state_file.read_text())
                proxy_pid = state.get("proxy_pid")
                if proxy_pid and not self._is_pid_alive(proxy_pid):
                    state_file.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError):
                # Corrupt file — remove it
                try:
                    state_file.unlink(missing_ok=True)
                except OSError:
                    pass

    async def _start_proxy(
        self,
        language: str,
        workspace: Path,
        server_config: dict,
        init_options: dict,
        idle_timeout: int = 300,
    ) -> dict:
        """Start a new proxy process and wait for it to be ready.

        Returns the state dict once the proxy is accepting connections.
        """
        command = server_config["command"]

        # Ensure state directory exists
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)

        # Launch proxy as detached process
        proxy_args = [
            sys.executable,
            "-m",
            "amplifier_module_tool_lsp.proxy",
            "--language",
            language,
            "--workspace",
            str(workspace),
            "--command",
            json.dumps(command),
            "--init-options",
            json.dumps(init_options),
            "--idle-timeout",
            str(idle_timeout),
            "--state-dir",
            str(self.STATE_DIR),
        ]

        subprocess.Popen(
            proxy_args,
            start_new_session=True,  # Detach from parent process group
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for proxy to write state file and start accepting connections
        state_path = self._state_file_path(language, workspace)
        for _ in range(60):  # Wait up to 30 seconds (60 * 0.5s)
            await asyncio.sleep(0.5)
            if state_path.exists():
                state = self._read_state(language, workspace)
                if state:
                    return state

        raise RuntimeError(
            f"LSP proxy for {language} at {workspace} failed to start within 30 seconds"
        )

    # ── Server management ────────────────────────────────────────────────

    def _server_key(self, language: str, workspace: Path) -> str:
        """Create unique key for server instance."""
        return f"{language}:{workspace}"

    def _validate_server_installation(
        self,
        language: str,
        install_check: list[str],
        server_config: dict[str, Any],
    ) -> None:
        """
        Validate that the LSP server is properly installed and working.

        Provides detailed diagnostics for common installation issues like
        stale wrapper scripts, broken shebangs, and shadowed installations.
        """
        import shutil

        cmd_name = install_check[0]  # e.g., "pyright"
        install_hint = server_config.get("install_hint", "")

        # Find where the command resolves to
        cmd_path = shutil.which(cmd_name)
        if not cmd_path:
            raise RuntimeError(
                f"{language} LSP server not found in PATH. {install_hint}"
            )

        # Check if it's a wrapper script pointing to a non-existent location
        try:
            with open(cmd_path, "r") as f:
                first_line = f.readline().strip()
                content_start = f.read(500)  # Read a bit more to check for paths

            # Check for stale wrapper scripts
            if first_line.startswith("#!"):
                # It's a script - check if it points to missing paths
                if (
                    "/opt/homebrew/Cellar/" in content_start
                    or "/usr/local/Cellar/" in content_start
                ):
                    # Likely a stale wrapper from old Homebrew install
                    raise RuntimeError(
                        f"{language} LSP server at {cmd_path} appears to be a stale wrapper script "
                        f"pointing to a removed Homebrew installation. "
                        f"To fix, remove the stale wrapper and reinstall:\n"
                        f"  rm {cmd_path}\n"
                        f"  {install_hint}"
                    )
        except (OSError, UnicodeDecodeError):
            # Binary file or can't read - that's fine, proceed with execution check
            pass

        # Actually run the install check command
        try:
            result = subprocess.run(
                install_check,
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode() if result.stderr else ""
                stdout = result.stdout.decode() if result.stdout else ""
                output = stderr or stdout

                # Check for "Cannot find module" error (stale npm wrapper)
                if "Cannot find module" in output:
                    raise RuntimeError(
                        f"{language} LSP server at {cmd_path} has a broken installation. "
                        f"Error: {output.strip()}\n"
                        f"This usually means a stale wrapper script. To fix:\n"
                        f"  rm {cmd_path}\n"
                        f"  Also check: rm {cmd_path.replace(cmd_name, cmd_name + '-langserver')} 2>/dev/null\n"
                        f"  Then reinstall: {install_hint}"
                    )

                # Check for bad interpreter
                if "bad interpreter" in output or "No such file or directory" in output:
                    raise RuntimeError(
                        f"{language} LSP server at {cmd_path} has a broken shebang. "
                        f"Error: {output.strip()}\n"
                        f"To fix, remove and reinstall:\n"
                        f"  rm {cmd_path}\n"
                        f"  {install_hint}"
                    )

                # Generic failure with full output
                raise RuntimeError(
                    f"{language} LSP server check failed.\n"
                    f"Command: {' '.join(install_check)}\n"
                    f"Path: {cmd_path}\n"
                    f"Output: {output.strip()}\n"
                    f"{install_hint}"
                )

        except FileNotFoundError:
            raise RuntimeError(
                f"{language} LSP server not found. "
                f"Attempted path: {cmd_path}\n"
                f"{install_hint}"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"{language} LSP server check timed out (>10s). "
                f"Path: {cmd_path}\n"
                f"The server may be broken. Try reinstalling: {install_hint}"
            )

    async def get_server(
        self,
        language: str,
        workspace: Path,
        server_config: dict[str, Any],
        init_options: dict[str, Any],
    ) -> LspServer:
        """Get or create an LSP server for the workspace."""
        key = self._server_key(language, workspace)

        if key not in self._servers:
            # Check if server is installed AND working
            install_check = server_config.get("install_check")
            if install_check:
                self._validate_server_installation(
                    language=language,
                    install_check=install_check,
                    server_config=server_config,
                )

            # Create new server
            server = await LspServer.create(
                language=language,
                workspace=workspace,
                command=server_config["command"],
                init_options=init_options,
                timeout=self._timeout,
            )
            self._servers[key] = server

        return self._servers[key]

    async def shutdown_all(self):
        """Shutdown all managed servers and clear caches."""
        for server in self._servers.values():
            await server.shutdown()
        self._servers.clear()
        # Notify operations layer to clear document tracking
        if self._on_shutdown:
            self._on_shutdown()
