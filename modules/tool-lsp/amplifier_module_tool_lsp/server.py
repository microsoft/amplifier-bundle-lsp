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
    ):
        self.language = language
        self.workspace = workspace
        self._process = process
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None

    @classmethod
    async def create(
        cls,
        language: str,
        workspace: Path,
        command: list[str],
        init_options: dict[str, Any],
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

        server = cls(language, workspace, process)
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
                        "documentSymbol": {"dynamicRegistration": True},
                        "implementation": {"dynamicRegistration": True},
                        "callHierarchy": {"dynamicRegistration": True},
                    },
                    "workspace": {
                        "symbol": {"dynamicRegistration": True},
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

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        await self._send(message)

        return await asyncio.wait_for(future, timeout=30.0)

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

                    # Handle response
                    if "id" in message and message["id"] in self._pending:
                        future = self._pending.pop(message["id"])
                        if "error" in message:
                            future.set_exception(Exception(message["error"].get("message", "Unknown error")))
                        else:
                            future.set_result(message.get("result"))

            except asyncio.CancelledError:
                break
            except Exception:
                continue

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

    def __init__(self):
        self._servers: dict[str, LspServer] = {}

    def _server_key(self, language: str, workspace: Path) -> str:
        """Create unique key for server instance."""
        return f"{language}:{workspace}"

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
                try:
                    result = subprocess.run(
                        install_check,
                        capture_output=True,
                        check=True,
                        timeout=10,
                    )
                except FileNotFoundError:
                    raise RuntimeError(
                        f"{language} LSP server not installed. "
                        f"{server_config.get('install_hint', '')}"
                    )
                except subprocess.CalledProcessError as e:
                    # Check for common issues like broken shebang
                    stderr = e.stderr.decode() if e.stderr else ""
                    if "bad interpreter" in stderr or "No such file or directory" in stderr:
                        raise RuntimeError(
                            f"{language} LSP server has a broken installation (bad interpreter). "
                            f"Try reinstalling: {server_config.get('install_hint', '')} "
                            f"If installed via Homebrew, try: npm install -g pyright"
                        )
                    raise RuntimeError(
                        f"{language} LSP server check failed: {stderr or e}. "
                        f"{server_config.get('install_hint', '')}"
                    )
                except subprocess.TimeoutExpired:
                    raise RuntimeError(
                        f"{language} LSP server check timed out. Server may be broken. "
                        f"Try reinstalling: {server_config.get('install_hint', '')}"
                    )

            # Create new server
            server = await LspServer.create(
                language=language,
                workspace=workspace,
                command=server_config["command"],
                init_options=init_options,
            )
            self._servers[key] = server

        return self._servers[key]

    async def shutdown_all(self):
        """Shutdown all managed servers."""
        for server in self._servers.values():
            await server.shutdown()
        self._servers.clear()
