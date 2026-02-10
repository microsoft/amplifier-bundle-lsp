"""LSP Operation Implementations."""

from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .server import LspServer

# Default limits to prevent context overflow
DEFAULT_MAX_RESULTS = 50
DEFAULT_MAX_HOVER_CHARS = 6000

# Per-operation limits for operations that can return arrays
LIMIT_GO_TO_DEFINITION = 20  # Overloads/partial classes
LIMIT_GO_TO_IMPLEMENTATION = 30  # Interface implementations
LIMIT_PREPARE_CALL_HIERARCHY = 10  # Call hierarchy items


class LspOperations:
    """Implements LSP operations."""

    def __init__(
        self,
        server_manager,
        max_results: int = DEFAULT_MAX_RESULTS,
        max_hover_chars: int = DEFAULT_MAX_HOVER_CHARS,
    ):
        self._server_manager = server_manager
        self._max_results = max_results
        self._max_hover_chars = max_hover_chars
        self._open_documents: dict[str, int] = {}  # URI → content hash
        self._doc_versions: dict[str, int] = {}  # URI → version number

    def _truncate_hover(self, result: dict | None) -> dict:
        """Truncate hover content and return with metadata.

        Hover results contain markdown content that can be very large
        (complex type signatures, long docstrings). Truncate to prevent
        context overflow while preserving useful information.
        """
        if result is None:
            return {
                "contents": None,
                "truncated": False,
                "message": "hover: No information available",
            }

        # Extract the content value - hover can return various formats
        contents = result.get("contents")
        if contents is None:
            return {
                "contents": None,
                "truncated": False,
                "message": "hover: No information available",
            }

        # Handle MarkupContent format: {"kind": "markdown", "value": "..."}
        if isinstance(contents, dict) and "value" in contents:
            value = contents["value"]
            original_len = len(value)

            if original_len > self._max_hover_chars:
                truncated_value = value[: self._max_hover_chars]
                # Try to truncate at a newline for cleaner output
                last_newline = truncated_value.rfind("\n", self._max_hover_chars - 500)
                if last_newline > self._max_hover_chars // 2:
                    truncated_value = truncated_value[:last_newline]

                return {
                    "contents": {**contents, "value": truncated_value},
                    "range": result.get("range"),
                    "truncated": True,
                    "original_length": original_len,
                    "message": f"hover: Showing {len(truncated_value)} of {original_len} chars (use read_file for full content)",
                }
            else:
                return {
                    "contents": contents,
                    "range": result.get("range"),
                    "truncated": False,
                    "message": f"hover: {original_len} chars",
                }

        # Handle plain string content
        if isinstance(contents, str):
            original_len = len(contents)
            if original_len > self._max_hover_chars:
                truncated = contents[: self._max_hover_chars]
                return {
                    "contents": truncated,
                    "range": result.get("range"),
                    "truncated": True,
                    "original_length": original_len,
                    "message": f"hover: Showing {len(truncated)} of {original_len} chars (use read_file for full content)",
                }
            else:
                return {
                    "contents": contents,
                    "range": result.get("range"),
                    "truncated": False,
                    "message": f"hover: {original_len} chars",
                }

        # Handle MarkedString array or other formats - pass through with warning
        return {
            "contents": contents,
            "range": result.get("range"),
            "truncated": False,
            "message": "hover: Complex format (not truncated)",
        }

    def _truncate_results(
        self, results: list | None, operation: str, max_results: int | None = None
    ) -> dict:
        """Truncate list results and return with metadata.

        Returns a dict with:
        - results: The (possibly truncated) list
        - total_count: Original count before truncation
        - truncated: Whether results were truncated
        - message: Human-readable summary

        Args:
            results: The list of results to truncate
            operation: Name of the operation for message formatting
            max_results: Optional per-operation limit (defaults to self._max_results)
        """
        if results is None:
            return {
                "results": [],
                "total_count": 0,
                "truncated": False,
                "message": f"{operation}: No results found",
            }

        if not isinstance(results, list):
            # Non-list results pass through unchanged
            return results

        limit = max_results if max_results is not None else self._max_results
        total = len(results)
        truncated = total > limit

        if truncated:
            results = results[:limit]
            message = f"{operation}: Showing {limit} of {total} results (truncated to prevent context overflow)"
        else:
            message = f"{operation}: {total} result{'s' if total != 1 else ''}"

        return {
            "results": results,
            "total_count": total,
            "truncated": truncated,
            "message": message,
        }

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
        """Notify server about open/changed document with tracking.

        - First open: sends textDocument/didOpen, stores hash and version=1
        - Unchanged: skips (no notification sent)
        - Changed: sends textDocument/didChange with incremented version
        """
        path = Path(file_path).resolve()
        uri = path.as_uri()
        content = path.read_text()
        content_hash = hash(content)

        if uri not in self._open_documents:
            # New document — send didOpen
            self._doc_versions[uri] = 1
            self._open_documents[uri] = content_hash
            await server.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": server.language,
                        "version": 1,
                        "text": content,
                    }
                },
            )
        elif self._open_documents[uri] != content_hash:
            # Content changed — send didChange with full replacement
            self._doc_versions[uri] += 1
            self._open_documents[uri] = content_hash
            version = self._doc_versions[uri]
            await server.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": content}],
                },
            )
        # else: unchanged — skip

    def _uri_to_path(self, uri: str) -> str:
        """Convert a file:// URI to a filesystem path."""
        prefix = "file://"
        if uri.startswith(prefix):
            return unquote(uri[len(prefix) :])
        return uri

    def _text_document_position(
        self, file_path: str, line: int, character: int
    ) -> dict:
        """Create TextDocumentPositionParams."""
        return {
            "textDocument": {"uri": Path(file_path).resolve().as_uri()},
            "position": {"line": line - 1, "character": character - 1},  # 0-based
        }

    async def _op_goToDefinition(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None,
    ) -> Any:
        """Find where a symbol is defined."""
        result = await server.request(
            "textDocument/definition",
            self._text_document_position(file_path, line, character),
        )
        # LSP can return Location | Location[] | LocationLink[]
        if isinstance(result, list):
            return self._truncate_results(
                result, "goToDefinition", LIMIT_GO_TO_DEFINITION
            )
        return result  # Single location, no truncation needed

    async def _op_findReferences(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None,
    ) -> Any:
        """Find all references to a symbol."""
        params = self._text_document_position(file_path, line, character)
        params["context"] = {"includeDeclaration": True}
        results = await server.request("textDocument/references", params)
        return self._truncate_results(results, "findReferences")

    async def _op_hover(
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None,
    ) -> Any:
        """Get hover information for a symbol."""
        result = await server.request(
            "textDocument/hover",
            self._text_document_position(file_path, line, character),
        )
        return self._truncate_hover(result)

    async def _op_documentSymbol(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None,
    ) -> Any:
        """Get all symbols in a document."""
        results = await server.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": Path(file_path).resolve().as_uri()}},
        )
        return self._truncate_results(results, "documentSymbol")

    async def _op_workspaceSymbol(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None,
    ) -> Any:
        """Search for symbols across workspace."""
        results = await server.request(
            "workspace/symbol",
            {"query": query or ""},
        )
        return self._truncate_results(results, "workspaceSymbol")

    async def _op_goToImplementation(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None,
    ) -> Any:
        """Find implementations of an interface/abstract method."""
        result = await server.request(
            "textDocument/implementation",
            self._text_document_position(file_path, line, character),
        )
        # LSP can return Location | Location[] | LocationLink[]
        if isinstance(result, list):
            return self._truncate_results(
                result, "goToImplementation", LIMIT_GO_TO_IMPLEMENTATION
            )
        return result  # Single location, no truncation needed

    async def _op_prepareCallHierarchy(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None,
    ) -> Any:
        """Prepare call hierarchy at position."""
        result = await server.request(
            "textDocument/prepareCallHierarchy",
            self._text_document_position(file_path, line, character),
        )
        # LSP returns CallHierarchyItem[] | null
        if isinstance(result, list):
            return self._truncate_results(
                result, "prepareCallHierarchy", LIMIT_PREPARE_CALL_HIERARCHY
            )
        return result  # null case, no truncation needed

    async def _op_incomingCalls(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None,
    ) -> Any:
        """Find functions that call the function at position."""
        # First get the call hierarchy item
        hierarchy_result = await self._op_prepareCallHierarchy(
            server, file_path, line, character, query
        )
        # Handle both dict (truncated) and raw result formats
        items = (
            hierarchy_result.get("results", [])
            if isinstance(hierarchy_result, dict)
            else hierarchy_result
        )
        if not items:
            return self._truncate_results([], "incomingCalls")

        results = await server.request(
            "callHierarchy/incomingCalls",
            {"item": items[0]},
        )
        return self._truncate_results(results, "incomingCalls")

    async def _op_outgoingCalls(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None,
    ) -> Any:
        """Find functions called by the function at position."""
        # First get the call hierarchy item
        hierarchy_result = await self._op_prepareCallHierarchy(
            server, file_path, line, character, query
        )
        # Handle both dict (truncated) and raw result formats
        items = (
            hierarchy_result.get("results", [])
            if isinstance(hierarchy_result, dict)
            else hierarchy_result
        )
        if not items:
            return self._truncate_results([], "outgoingCalls")

        results = await server.request(
            "callHierarchy/outgoingCalls",
            {"item": items[0]},
        )
        return self._truncate_results(results, "outgoingCalls")
