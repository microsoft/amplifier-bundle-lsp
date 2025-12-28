"""LSP Operation Implementations."""

from pathlib import Path
from typing import Any

from .server import LspServer


class LspOperations:
    """Implements LSP operations."""

    def __init__(self, server_manager):
        self._server_manager = server_manager

    async def execute(
        self,
        server: LspServer,
        operation: str,
        file_path: str,
        line: int,
        character: int,
        query: str | None = None,
    ) -> Any:
        """Execute an LSP operation."""
        # Open the document first
        await self._open_document(server, file_path)

        # Dispatch to specific operation
        method = getattr(self, f"_op_{operation}", None)
        if not method:
            raise ValueError(f"Unknown operation: {operation}")

        return await method(server, file_path, line, character, query)

    async def _open_document(self, server: LspServer, file_path: str):
        """Notify server about open document."""
        path = Path(file_path).resolve()
        content = path.read_text()

        await server.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path.as_uri(),
                    "languageId": server.language,
                    "version": 1,
                    "text": content,
                }
            },
        )

    def _text_document_position(self, file_path: str, line: int, character: int) -> dict:
        """Create TextDocumentPositionParams."""
        return {
            "textDocument": {"uri": Path(file_path).resolve().as_uri()},
            "position": {"line": line - 1, "character": character - 1},  # 0-based
        }

    async def _op_goToDefinition(  # noqa: N802 - matches LSP operation name
        self, server: LspServer, file_path: str, line: int, character: int, query: str | None
    ) -> Any:
        """Find where a symbol is defined."""
        return await server.request(
            "textDocument/definition",
            self._text_document_position(file_path, line, character),
        )

    async def _op_findReferences(  # noqa: N802 - matches LSP operation name
        self, server: LspServer, file_path: str, line: int, character: int, query: str | None
    ) -> Any:
        """Find all references to a symbol."""
        params = self._text_document_position(file_path, line, character)
        params["context"] = {"includeDeclaration": True}
        return await server.request("textDocument/references", params)

    async def _op_hover(self, server: LspServer, file_path: str, line: int, character: int, query: str | None) -> Any:
        """Get hover information for a symbol."""
        return await server.request(
            "textDocument/hover",
            self._text_document_position(file_path, line, character),
        )

    async def _op_documentSymbol(  # noqa: N802 - matches LSP operation name
        self, server: LspServer, file_path: str, line: int, character: int, query: str | None
    ) -> Any:
        """Get all symbols in a document."""
        return await server.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": Path(file_path).resolve().as_uri()}},
        )

    async def _op_workspaceSymbol(  # noqa: N802 - matches LSP operation name
        self, server: LspServer, file_path: str, line: int, character: int, query: str | None
    ) -> Any:
        """Search for symbols across workspace."""
        return await server.request(
            "workspace/symbol",
            {"query": query or ""},
        )

    async def _op_goToImplementation(  # noqa: N802 - matches LSP operation name
        self, server: LspServer, file_path: str, line: int, character: int, query: str | None
    ) -> Any:
        """Find implementations of an interface/abstract method."""
        return await server.request(
            "textDocument/implementation",
            self._text_document_position(file_path, line, character),
        )

    async def _op_prepareCallHierarchy(  # noqa: N802 - matches LSP operation name
        self, server: LspServer, file_path: str, line: int, character: int, query: str | None
    ) -> Any:
        """Prepare call hierarchy at position."""
        return await server.request(
            "textDocument/prepareCallHierarchy",
            self._text_document_position(file_path, line, character),
        )

    async def _op_incomingCalls(  # noqa: N802 - matches LSP operation name
        self, server: LspServer, file_path: str, line: int, character: int, query: str | None
    ) -> Any:
        """Find functions that call the function at position."""
        # First get the call hierarchy item
        items = await self._op_prepareCallHierarchy(server, file_path, line, character, query)
        if not items:
            return []

        return await server.request(
            "callHierarchy/incomingCalls",
            {"item": items[0]},
        )

    async def _op_outgoingCalls(  # noqa: N802 - matches LSP operation name
        self, server: LspServer, file_path: str, line: int, character: int, query: str | None
    ) -> Any:
        """Find functions called by the function at position."""
        # First get the call hierarchy item
        items = await self._op_prepareCallHierarchy(server, file_path, line, character, query)
        if not items:
            return []

        return await server.request(
            "callHierarchy/outgoingCalls",
            {"item": items[0]},
        )
