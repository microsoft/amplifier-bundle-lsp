"""LSP Operation Implementations."""

import asyncio
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
LIMIT_PREPARE_TYPE_HIERARCHY = 10  # Type hierarchy items


class LspOperations:
    """Implements LSP operations."""

    # Operations that don't require file_path (no _open_document needed)
    _FILE_PATH_OPTIONAL_OPS = {"customRequest", "workspaceSymbol"}

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

    def clear_document_tracking(self) -> None:
        """Clear document tracking state.

        Called when servers are shut down to prevent unbounded memory growth
        in long-running sessions. The next operation will re-open documents
        as needed.
        """
        self._open_documents.clear()
        self._doc_versions.clear()

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
            # Non-list results (e.g., single Location) wrapped in standard envelope
            return {
                "results": results,
                "total_count": 1,
                "truncated": False,
                "message": f"{operation}: 1 result",
            }

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

    def _truncate_custom_result(
        self, result: dict, method: str, max_str_len: int = 10000
    ) -> dict:
        """Truncate overly long string values in custom results."""
        truncated = False
        formatted = {}
        for key, value in result.items():
            if isinstance(value, str) and len(value) > max_str_len:
                formatted[key] = (
                    value[:max_str_len] + f"\n... (truncated from {len(value)} chars)"
                )
                truncated = True
            else:
                formatted[key] = value
        formatted["_method"] = method
        formatted["_truncated"] = truncated
        return formatted

    async def execute(
        self,
        server: LspServer,
        operation: str,
        file_path: str | None,
        line: int | None,
        character: int | None,
        query: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Execute an LSP operation."""
        # Dispatch to specific operation
        method = getattr(self, f"_op_{operation}", None)
        if not method:
            raise ValueError(f"Unknown operation: {operation}")

        # File-path-optional operations: don't need _open_document
        if operation in self._FILE_PATH_OPTIONAL_OPS:
            return await method(
                server,
                file_path=file_path,
                line=line,
                character=character,
                query=query,
                **kwargs,
            )

        # All other operations require file_path — open document first
        assert file_path is not None  # Caller should validate
        await self._open_document(server, file_path)
        try:
            return await method(server, file_path, line, character, query, **kwargs)
        except Exception as e:
            if "file not found" in str(e).lower() and file_path:
                # Retry once after brief delay — server may still be processing didOpen
                await asyncio.sleep(0.5)
                return await method(server, file_path, line, character, query, **kwargs)
            raise

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
            # Brief delay after first open — server may need time to index
            await asyncio.sleep(0.1)
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
        query: str | None = None,
        **kwargs: Any,
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
        query: str | None = None,
        **kwargs: Any,
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
        query: str | None = None,
        **kwargs: Any,
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
        query: str | None = None,
        **kwargs: Any,
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
        query: str | None = None,
        **kwargs: Any,
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
        query: str | None = None,
        **kwargs: Any,
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
        query: str | None = None,
        **kwargs: Any,
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
        query: str | None = None,
        **kwargs: Any,
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

    def _format_diagnostics(
        self, diagnostics: list, file_path: str, source: str = "pull"
    ) -> dict:
        """Format raw LSP diagnostics into structured output.

        Shared formatter for both pull and push diagnostics.
        Converts 0-based LSP positions to 1-based, maps severity numbers
        to labels, and caps output at 100 diagnostics.
        """
        if not diagnostics:
            return {
                "diagnostics": [],
                "count": 0,
                "errors": 0,
                "warnings": 0,
                "source": source,
                "message": f"diagnostics: No issues found in {Path(file_path).name}",
            }

        formatted = []
        errors = 0
        warnings = 0
        for d in diagnostics[:100]:  # Cap at 100
            severity = d.get("severity", 1)
            if severity == 1:
                errors += 1
                sev_label = "error"
            elif severity == 2:
                warnings += 1
                sev_label = "warning"
            elif severity == 3:
                sev_label = "info"
            else:
                sev_label = "hint"

            start = d.get("range", {}).get("start", {})
            end = d.get("range", {}).get("end", {})

            entry: dict = {
                "severity": sev_label,
                "message": d.get("message", ""),
                "line": start.get("line", 0) + 1,
                "character": start.get("character", 0) + 1,
                "end_line": end.get("line", 0) + 1,
                "end_character": end.get("character", 0) + 1,
            }

            # Include code if available (e.g., "E0308" for Rust type mismatch)
            if d.get("code") is not None:
                entry["code"] = d["code"]
            if d.get("source"):
                entry["source"] = d["source"]

            # Include related information if available
            related = d.get("relatedInformation", [])
            if related:
                entry["related"] = [
                    {
                        "message": r.get("message", ""),
                        "file": self._uri_to_path(r.get("location", {}).get("uri", "")),
                        "line": r.get("location", {})
                        .get("range", {})
                        .get("start", {})
                        .get("line", 0)
                        + 1,
                    }
                    for r in related[:5]  # Cap related info
                ]

            formatted.append(entry)

        return {
            "diagnostics": formatted,
            "count": len(formatted),
            "errors": errors,
            "warnings": warnings,
            "truncated": len(diagnostics) > 100,
            "total_count": len(diagnostics),
            "source": source,
            "message": f"diagnostics: {errors} error(s), {warnings} warning(s) in {Path(file_path).name}",
        }

    async def _op_diagnostics(
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Get diagnostics for a file (pull with push fallback)."""
        uri = Path(file_path).resolve().as_uri()

        # Ensure document is open with latest content
        await self._open_document(server, file_path)

        # Try pull diagnostics first (LSP 3.17)
        try:
            result = await server.request(
                "textDocument/diagnostic",
                {"textDocument": {"uri": uri}},
            )
            items = result.get("items", []) if result else []
            return self._format_diagnostics(items, file_path, source="pull")
        except Exception:
            pass

        # Fall back to push cache
        cached = server.get_cached_diagnostics(uri)
        if cached is not None:
            return self._format_diagnostics(cached, file_path, source="push_cache")

        # No diagnostics available from either source
        # Give the server a moment — diagnostics may not have arrived yet
        await asyncio.sleep(2)

        cached = server.get_cached_diagnostics(uri)
        if cached is not None:
            return self._format_diagnostics(cached, file_path, source="push_cache")

        return {
            "diagnostics": [],
            "count": 0,
            "source": "none",
            "message": "diagnostics: No diagnostics available. Server may still be analyzing — try again in a few seconds.",
        }

    async def _op_outgoingCalls(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None = None,
        **kwargs: Any,
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

    async def _prepare_type_hierarchy(
        self, server: LspServer, file_path: str, line: int, character: int
    ) -> list | None:
        """Shared prepare step for supertypes/subtypes."""
        await self._open_document(server, file_path)
        result = await server.request(
            "textDocument/prepareTypeHierarchy",
            {
                "textDocument": {"uri": Path(file_path).resolve().as_uri()},
                "position": {"line": line - 1, "character": character - 1},
            },
        )
        return result if isinstance(result, list) and result else None

    async def _op_prepareTypeHierarchy(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Prepare type hierarchy at position."""
        await self._open_document(server, file_path)
        result = await server.request(
            "textDocument/prepareTypeHierarchy",
            self._text_document_position(file_path, line, character),
        )
        return self._truncate_results(
            result, "prepareTypeHierarchy", LIMIT_PREPARE_TYPE_HIERARCHY
        )

    async def _op_supertypes(
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Find supertypes (implemented traits, supertraits) of type at position."""
        items = await self._prepare_type_hierarchy(server, file_path, line, character)
        if not items:
            return {
                "results": [],
                "message": "supertypes: No type hierarchy item at this position",
            }
        result = await server.request("typeHierarchy/supertypes", {"item": items[0]})
        return self._truncate_results(result, "supertypes")

    async def _op_subtypes(
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Find subtypes (implementors, sub-structs) of type at position."""
        items = await self._prepare_type_hierarchy(server, file_path, line, character)
        if not items:
            return {
                "results": [],
                "message": "subtypes: No type hierarchy item at this position",
            }
        result = await server.request("typeHierarchy/subtypes", {"item": items[0]})
        return self._truncate_results(result, "subtypes")

    async def _op_inlayHints(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Get inlay hints (inferred types, parameter names, lifetimes) for a range."""
        uri = Path(file_path).resolve().as_uri()

        end_line = kwargs.get("end_line") or (line + 50)
        end_char = kwargs.get("end_character") or 1

        result = await server.request(
            "textDocument/inlayHint",
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": line - 1, "character": character - 1},
                    "end": {"line": end_line - 1, "character": end_char - 1},
                },
            },
        )

        if not result:
            return {
                "hints": [],
                "count": 0,
                "message": "inlayHints: No hints in this range",
            }

        formatted = []
        for hint in result[:100]:  # Cap at 100 hints
            position = hint.get("position", {})

            # Handle label — can be string or InlayHintLabelPart[]
            label = hint.get("label", "")
            if isinstance(label, list):
                label = "".join(part.get("value", "") for part in label)

            # Kind: 1 = Type, 2 = Parameter
            kind = hint.get("kind")
            kind_label = {1: "type", 2: "parameter"}.get(kind, "other")

            formatted.append(
                {
                    "line": position.get("line", 0) + 1,
                    "character": position.get("character", 0) + 1,
                    "label": label,
                    "kind": kind_label,
                }
            )

        # Group by kind for summary
        type_count = sum(1 for h in formatted if h["kind"] == "type")
        param_count = sum(1 for h in formatted if h["kind"] == "parameter")
        other_count = sum(1 for h in formatted if h["kind"] == "other")

        parts = []
        if type_count:
            parts.append(f"{type_count} type(s)")
        if param_count:
            parts.append(f"{param_count} parameter name(s)")
        if other_count:
            parts.append(f"{other_count} other")
        summary = ", ".join(parts) if parts else "no hints"

        return {
            "hints": formatted,
            "count": len(formatted),
            "type_hints": type_count,
            "parameter_hints": param_count,
            "truncated": len(result) > 100,
            "total_count": len(result),
            "message": f"inlayHints: {summary} in range",
        }

    async def _op_customRequest(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str | None = None,
        line: int | None = None,
        character: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Send a custom/extension LSP request. For server-specific features."""
        method = kwargs.get("customMethod")
        if not method:
            return {"error": "customRequest requires 'customMethod' parameter"}

        params = kwargs.get("customParams", {})

        # If file_path provided, open document and add textDocument to params if not already present
        if file_path:
            await self._open_document(server, file_path)
            uri = Path(file_path).resolve().as_uri()
            if "textDocument" not in params:
                params["textDocument"] = {"uri": uri}

        # If position provided, add it to params if not already present
        if line and character and "position" not in params:
            params["position"] = {"line": line - 1, "character": character - 1}

        try:
            result = await server.request(method, params)
        except Exception as e:
            return {
                "error": f"Custom request '{method}' failed: {e!s}",
                "_method": method,
            }

        # Format result based on type
        if result is None:
            return {
                "result": None,
                "_method": method,
                "message": f"custom:{method}: completed (null result)",
            }
        elif isinstance(result, list):
            return self._truncate_results(result, f"custom:{method}")
        elif isinstance(result, dict):
            return self._truncate_custom_result(result, method)
        else:
            return {
                "result": result,
                "_method": method,
                "message": f"custom:{method}: completed",
            }

    def _format_workspace_edit(self, workspace_edit: dict, description: str) -> dict:
        """Format a WorkspaceEdit for AI consumption.

        Handles both `changes` (simple) and `documentChanges` (versioned) formats.
        Returns structured data — does NOT apply edits.
        """
        edits: list[dict] = []
        files_affected: set[str] = set()

        # Handle "changes" format: {uri: TextEdit[]}
        changes = workspace_edit.get("changes", {})
        if changes:
            for uri, file_edits in changes.items():
                file_path = self._uri_to_path(uri)
                files_affected.add(file_path)
                for edit in file_edits:
                    start = edit.get("range", {}).get("start", {})
                    end = edit.get("range", {}).get("end", {})
                    edits.append(
                        {
                            "file": file_path,
                            "start_line": start.get("line", 0) + 1,
                            "start_character": start.get("character", 0) + 1,
                            "end_line": end.get("line", 0) + 1,
                            "end_character": end.get("character", 0) + 1,
                            "new_text": edit.get("newText", ""),
                        }
                    )

        # Handle "documentChanges" format: TextDocumentEdit[] | CreateFile | RenameFile | DeleteFile
        document_changes = workspace_edit.get("documentChanges", [])
        if document_changes and not changes:  # Only if changes wasn't populated
            for doc_change in document_changes:
                if "textDocument" in doc_change:
                    # TextDocumentEdit
                    uri = doc_change["textDocument"]["uri"]
                    file_path = self._uri_to_path(uri)
                    files_affected.add(file_path)
                    for edit in doc_change.get("edits", []):
                        start = edit.get("range", {}).get("start", {})
                        end = edit.get("range", {}).get("end", {})
                        edits.append(
                            {
                                "file": file_path,
                                "start_line": start.get("line", 0) + 1,
                                "start_character": start.get("character", 0) + 1,
                                "end_line": end.get("line", 0) + 1,
                                "end_character": end.get("character", 0) + 1,
                                "new_text": edit.get("newText", ""),
                            }
                        )
                elif "kind" in doc_change:
                    # Resource operation (create/rename/delete file)
                    kind = doc_change["kind"]
                    if kind == "rename":
                        edits.append(
                            {
                                "file_operation": "rename",
                                "old_path": self._uri_to_path(
                                    doc_change.get("oldUri", "")
                                ),
                                "new_path": self._uri_to_path(
                                    doc_change.get("newUri", "")
                                ),
                            }
                        )
                        files_affected.add(
                            self._uri_to_path(doc_change.get("oldUri", ""))
                        )
                    elif kind == "create":
                        edits.append(
                            {
                                "file_operation": "create",
                                "path": self._uri_to_path(doc_change.get("uri", "")),
                            }
                        )
                    elif kind == "delete":
                        edits.append(
                            {
                                "file_operation": "delete",
                                "path": self._uri_to_path(doc_change.get("uri", "")),
                            }
                        )

        total = len(edits)
        truncated = total > 200
        return {
            "description": description,
            "files_affected": len(files_affected),
            "total_edits": total,
            "edits": edits[:200],  # Cap at 200 edits
            "truncated": truncated,
            "message": f"{total} edit(s) across {len(files_affected)} file(s)",
        }

    async def _op_rename(
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Rename a symbol across the workspace. Returns edits, does not apply them."""
        new_name = kwargs.get("newName")
        if not new_name:
            return {"error": "rename requires 'newName' parameter"}

        uri = Path(file_path).resolve().as_uri()
        position = {"line": line - 1, "character": character - 1}

        await self._open_document(server, file_path)

        # Step 1: Check if rename is valid at this position
        try:
            prepare = await server.request(
                "textDocument/prepareRename",
                {
                    "textDocument": {"uri": uri},
                    "position": position,
                },
            )
            if prepare is None:
                return {
                    "success": False,
                    "message": "rename: Symbol at this position cannot be renamed",
                }
        except Exception:
            pass  # Server may not support prepareRename — proceed to rename

        # Step 2: Perform rename
        result = await server.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": position,
                "newName": new_name,
            },
        )

        if not result:
            return {
                "success": False,
                "message": "rename: No edits returned. Symbol may not be renameable.",
            }

        formatted = self._format_workspace_edit(result, f"Rename to '{new_name}'")
        formatted["operation"] = "rename"
        formatted["new_name"] = new_name
        return formatted

    def _diagnostic_in_range(self, diagnostic: dict, start: dict, end: dict) -> bool:
        """Check if a diagnostic overlaps with the given range."""
        d_range = diagnostic.get("range", {})
        d_start = d_range.get("start", {})
        d_end = d_range.get("end", {})
        # Overlap: diagnostic starts before range ends AND diagnostic ends after range starts
        return d_start.get("line", 0) <= end.get("line", 0) and d_end.get(
            "line", 0
        ) >= start.get("line", 0)

    async def _op_codeAction(  # noqa: N802 - matches LSP operation name
        self,
        server: LspServer,
        file_path: str,
        line: int,
        character: int,
        query: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Get available code actions (fixes, refactorings) at a position or range."""
        uri = Path(file_path).resolve().as_uri()

        await self._open_document(server, file_path)

        # Build range
        start = {"line": line - 1, "character": character - 1}
        end_line = kwargs.get("end_line")
        end_char = kwargs.get("end_character")
        if end_line and end_char:
            end = {"line": end_line - 1, "character": end_char - 1}
        else:
            end = start  # Point range

        # Get cached diagnostics for context
        cached = server.get_cached_diagnostics(uri) or []
        relevant = [d for d in cached if self._diagnostic_in_range(d, start, end)]

        result = await server.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": {"start": start, "end": end},
                "context": {
                    "diagnostics": relevant,
                    "triggerKind": 1,  # Invoked (not automatic)
                },
            },
        )

        if not result:
            return {
                "actions": [],
                "count": 0,
                "message": "codeAction: No actions available at this position",
            }

        formatted = []
        for action in result[:30]:  # Cap at 30 actions
            entry = {
                "title": action.get("title", ""),
                "kind": action.get("kind", ""),
                "is_preferred": action.get("isPreferred", False),
            }

            # If action has a direct edit, format it
            if "edit" in action:
                entry["edit"] = self._format_workspace_edit(
                    action["edit"], action.get("title", "")
                )

            # If action has a command (server-side execution)
            if "command" in action and "edit" not in action:
                entry["has_command"] = True
                entry["command_title"] = action["command"].get("title", "")

            # How many diagnostics this fixes
            if "diagnostics" in action:
                entry["fixes_diagnostics"] = len(action["diagnostics"])

            formatted.append(entry)

        # Sort: preferred first, then quick fixes, then refactorings
        formatted.sort(
            key=lambda a: (
                not a.get("is_preferred", False),
                not a.get("kind", "").startswith("quickfix"),
            )
        )

        return {
            "actions": formatted,
            "count": len(formatted),
            "truncated": len(result) > 30,
            "total_count": len(result),
            "message": f"codeAction: {len(formatted)} action(s) available",
        }
