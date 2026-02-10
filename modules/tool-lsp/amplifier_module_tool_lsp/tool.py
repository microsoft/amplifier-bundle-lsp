"""
LSP Tool Implementation

This tool has ZERO hardcoded language knowledge. All language support comes
from configuration provided by language bundles via deep merge.
"""

from pathlib import Path

from amplifier_core.models import ToolResult

from .operations import LspOperations
from .server import LspServerManager


class LspTool:
    """Generic LSP tool - all language knowledge from configuration."""

    # Supported operations (grouped by category)
    OPERATIONS = [
        # Navigation
        "goToDefinition",
        "findReferences",
        "hover",
        "documentSymbol",
        "workspaceSymbol",
        "goToImplementation",
        # Call hierarchy
        "prepareCallHierarchy",
        "incomingCalls",
        "outgoingCalls",
        # Type hierarchy
        "prepareTypeHierarchy",
        "supertypes",
        "subtypes",
        # Verification
        "diagnostics",
        # Refactoring
        "rename",
        "codeAction",
        # Inspection
        "inlayHints",
        # Extensions
        "customRequest",
    ]

    # Operations that require line/character position params
    POSITION_REQUIRED_OPS = {
        "goToDefinition",
        "findReferences",
        "hover",
        "goToImplementation",
        "prepareCallHierarchy",
        "incomingCalls",
        "outgoingCalls",
        "prepareTypeHierarchy",
        "supertypes",
        "subtypes",
        "rename",
        "codeAction",
        "inlayHints",
    }

    # Operations added in Task 1 that are not yet implemented
    _NOT_YET_IMPLEMENTED_OPS: set[str] = set()

    # Operations that don't require file_path (use first configured language + cwd)
    FILE_PATH_OPTIONAL_OPS = {"customRequest", "workspaceSymbol"}

    def __init__(self, config: dict):
        """Initialize with configuration containing language definitions."""
        self._languages = config.get("languages", {})
        self._timeout = config.get("timeout_seconds", 30)
        self._max_results = config.get("max_results", 50)  # Prevent context overflow
        self._max_hover_chars = config.get(
            "max_hover_chars", 6000
        )  # Limit hover content size
        self._server_manager = LspServerManager(timeout=float(self._timeout))
        self._operations = LspOperations(
            self._server_manager,
            max_results=self._max_results,
            max_hover_chars=self._max_hover_chars,
        )
        # Wire cleanup: when servers shut down, clear document tracking to bound memory
        self._server_manager._on_shutdown = self._operations.clear_document_tracking

    @property
    def name(self) -> str:
        return "LSP"

    @property
    def description(self) -> str:
        lang_names = ", ".join(self._languages.keys()) if self._languages else "none"
        return (
            f"Interact with Language Server Protocol servers for code intelligence. "
            f"Configured languages: {lang_names}.\n\n"
            "NAVIGATION operations:\n"
            "  goToDefinition, findReferences, hover, documentSymbol, workspaceSymbol,\n"
            "  goToImplementation, prepareCallHierarchy, incomingCalls, outgoingCalls\n\n"
            "TYPE HIERARCHY operations:\n"
            "  prepareTypeHierarchy, supertypes, subtypes\n\n"
            "VERIFICATION operations:\n"
            "  diagnostics - get compiler errors/warnings for a file\n\n"
            "REFACTORING operations:\n"
            "  rename - cross-file semantic rename (returns edits, does not apply)\n"
            "  codeAction - suggested fixes and refactorings (returns edits)\n\n"
            "INSPECTION operations:\n"
            "  inlayHints - inferred types/parameter names for a range\n\n"
            "EXTENSION operations:\n"
            "  customRequest - send any server-specific LSP method\n\n"
            "WHEN TO USE LSP vs GREP:\n"
            "- Find callers of a function: incomingCalls (semantic) vs grep (matches strings/comments)\n"
            "- Find where symbol is defined: goToDefinition (precise) vs grep (multiple matches)\n"
            "- Get type info or signature: hover (full type data) - grep cannot do this\n"
            "- Check for errors after editing: diagnostics - grep cannot do this\n"
            "- Rename a symbol safely: rename (cross-file, semantic) vs grep (text-only, unsafe)\n"
            "- Find text pattern anywhere: use grep instead (faster for text search)\n"
            "- Search across many files: use grep instead (faster for bulk search)\n\n"
            "RULE: Use LSP for semantic code understanding (types, references, call chains, "
            "diagnostics, refactoring). Use grep for text pattern matching.\n\n"
            "For complex multi-step navigation tasks, delegate to the appropriate "
            "language-specific code-intel agent."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": self.OPERATIONS,
                    "description": (
                        "The LSP operation to perform. "
                        "Navigation: goToDefinition, findReferences, hover, documentSymbol, "
                        "workspaceSymbol, goToImplementation. "
                        "Call hierarchy: prepareCallHierarchy, incomingCalls, outgoingCalls. "
                        "Type hierarchy: prepareTypeHierarchy, supertypes, subtypes. "
                        "Verification: diagnostics. "
                        "Refactoring: rename (needs newName), codeAction. "
                        "Inspection: inlayHints (needs end_line, end_character). "
                        "Extension: customRequest (needs customMethod)."
                    ),
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file",
                },
                "line": {
                    "type": "integer",
                    "description": "Line number (1-based)",
                    "minimum": 1,
                },
                "character": {
                    "type": "integer",
                    "description": "Character offset (1-based)",
                    "minimum": 1,
                },
                "query": {
                    "type": "string",
                    "description": "Search query for workspaceSymbol operation",
                },
                "newName": {
                    "type": "string",
                    "description": "New name for rename operation (required for rename)",
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line for range operations: codeAction, inlayHints (1-based)",
                    "minimum": 1,
                },
                "end_character": {
                    "type": "integer",
                    "description": "End character for range operations: codeAction, inlayHints (1-based)",
                    "minimum": 1,
                },
                "customMethod": {
                    "type": "string",
                    "description": "LSP method name for customRequest (e.g., 'server/someExtension')",
                },
                "customParams": {
                    "type": "object",
                    "description": "Parameters object for customRequest",
                },
            },
            "required": ["operation"],
        }

    def _detect_language(self, file_path: str) -> str | None:
        """Detect language from file extension using configured mappings."""
        ext = Path(file_path).suffix.lower()
        for lang_name, lang_config in self._languages.items():
            if ext in lang_config.get("extensions", []):
                return lang_name
        return None

    def _find_workspace(self, file_path: str, lang_config: dict) -> Path:
        """Find workspace root by looking for marker files."""
        path = Path(file_path).resolve()
        markers = lang_config.get("workspace_markers", [])

        # Walk up directory tree looking for markers
        current = path.parent
        while current != current.parent:
            for marker in markers:
                if (current / marker).exists():
                    return current
            current = current.parent

        # Fallback to file's directory
        return path.parent

    async def execute(self, arguments: dict) -> ToolResult:
        """Execute an LSP operation."""
        operation = arguments.get("operation")
        file_path = arguments.get("file_path")
        line = arguments.get("line")
        character = arguments.get("character")

        # Validate operation
        if operation not in self.OPERATIONS:
            return ToolResult(
                success=False,
                error={
                    "message": f"Unknown operation: {operation}. Valid: {self.OPERATIONS}"
                },
            )

        # Per-operation parameter validation
        if operation == "customRequest" and not arguments.get("customMethod"):
            return ToolResult(
                success=False,
                output="customRequest requires 'customMethod' parameter (e.g., 'rust-analyzer/expandMacro')",
            )

        # Validate required parameters — file_path is optional for some operations
        if not file_path and operation not in self.FILE_PATH_OPTIONAL_OPS:
            return ToolResult(
                success=False,
                error={"message": "file_path is required"},
            )

        # Validate position for operations that need it
        if operation in self.POSITION_REQUIRED_OPS:
            if line is None or character is None:
                return ToolResult(
                    success=False,
                    error={
                        "message": f"line and character position required for {operation}"
                    },
                )

        # Per-operation parameter validation (after position check)
        if operation == "rename" and not arguments.get("newName"):
            return ToolResult(
                success=False,
                error={"message": "rename requires 'newName' parameter"},
            )

        # Return early for not-yet-implemented operations
        if operation in self._NOT_YET_IMPLEMENTED_OPS:
            return ToolResult(
                success=False,
                error={"message": f"Operation '{operation}' is not yet implemented"},
            )

        # Default position values for operations that don't need them
        if line is None:
            line = 1
        if character is None:
            character = 1

        # Handle file_path-optional operations — use first configured language + cwd
        if operation in self.FILE_PATH_OPTIONAL_OPS and not file_path:
            if not self._languages:
                return ToolResult(
                    success=False,
                    error={"message": "No languages configured"},
                )
            language = next(iter(self._languages))
            lang_config = self._languages[language]
            workspace = Path.cwd()
        else:
            # Normal flow: detect language from file extension
            assert file_path is not None  # Guaranteed by file_path check above
            language = self._detect_language(file_path)
            if not language:
                configured = list(self._languages.keys()) or ["none"]
                return ToolResult(
                    success=False,
                    error={
                        "message": f"No LSP support configured for {file_path}. Configured languages: {', '.join(configured)}"
                    },
                )

            # Get language configuration
            lang_config = self._languages[language]

            # Find workspace root
            workspace = self._find_workspace(file_path, lang_config)

        # Get or create server for this workspace
        try:
            server = await self._server_manager.get_server(
                language=language,
                workspace=workspace,
                server_config=lang_config["server"],
                init_options=lang_config.get("initialization_options", {}),
            )
        except Exception as e:
            install_hint = lang_config["server"].get("install_hint", "")
            return ToolResult(
                success=False,
                error={
                    "message": f"Failed to start {language} LSP server: {e}. {install_hint}"
                },
            )

        # Execute the operation
        try:
            result = await self._operations.execute(
                server=server,
                operation=operation,
                file_path=file_path,
                line=line,
                character=character,
                query=arguments.get("query"),
                newName=arguments.get("newName"),
                customMethod=arguments.get("customMethod"),
                customParams=arguments.get("customParams"),
                end_line=arguments.get("end_line"),
                end_character=arguments.get("end_character"),
            )
            return ToolResult(success=True, output=result)
        except Exception as e:
            error_msg = str(e) or type(e).__name__
            return ToolResult(
                success=False,
                error={"message": f"LSP operation failed: {error_msg}"},
            )

    async def cleanup(self):
        """Shutdown all LSP servers."""
        await self._server_manager.shutdown_all()
