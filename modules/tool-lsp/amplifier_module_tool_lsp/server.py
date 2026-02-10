"""LSP Server Management - handles server lifecycle.

Uses structured concurrency pattern for subprocess cleanup to prevent
"Event loop is closed" errors. See: https://github.com/python/cpython/issues/114177

The key insight: always await process.wait() (or communicate()) to ensure the
event loop registers process termination before returning.
"""

import asyncio
import contextlib
import json
import subprocess
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

    def __init__(self, timeout: float = 30.0, on_shutdown: Any = None):
        self._servers: dict[str, LspServer] = {}
        self._timeout = timeout
        self._on_shutdown = on_shutdown

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
