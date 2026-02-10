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

    # Supported operations
    OPERATIONS = [
        "goToDefinition",
        "findReferences",
        "hover",
        "documentSymbol",
        "workspaceSymbol",
        "goToImplementation",
        "prepareCallHierarchy",
        "incomingCalls",
        "outgoingCalls",
        "prepareTypeHierarchy",
        "supertypes",
        "subtypes",
        "diagnostics",
        "rename",
        "codeAction",
        "inlayHints",
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

    @property
    def name(self) -> str:
        return "LSP"

    @property
    def description(self) -> str:
        configured = list(self._languages.keys()) if self._languages else ["none"]
        return (
            f"Interact with Language Server Protocol servers for code intelligence. "
            f"Configured languages: {', '.join(configured)}. "
            f"Operations: {', '.join(self.OPERATIONS)}\n\n"
            f"WHEN TO USE LSP vs GREP:\n"
            f"- Find all callers of a function: incomingCalls (semantic) vs grep (may match strings/comments)\n"
            f"- Find where symbol is defined: goToDefinition (precise) vs grep (multiple matches)\n"
            f"- Get type info or signature: hover (full type data) - grep cannot do this\n"
            f"- Find text pattern anywhere: use grep instead (faster for text search)\n"
            f"- Search across many files: use grep instead (faster for bulk search)\n\n"
            f"RULE: Use LSP for semantic code understanding (types, references, call chains). "
            f"Use grep for text pattern matching.\n\n"
            f"For complex multi-step navigation tasks, delegate to lsp:code-navigator or "
            f"lsp-python:python-code-intel agents."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": self.OPERATIONS,
                    "description": "The LSP operation to perform",
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
                    "description": "New name for rename operation",
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line for range-based operations (codeAction, inlayHints). 1-based.",
                    "minimum": 1,
                },
                "end_character": {
                    "type": "integer",
                    "description": "End character for range-based operations. 1-based.",
                    "minimum": 1,
                },
                "customMethod": {
                    "type": "string",
                    "description": "LSP method name for customRequest (e.g., 'rust-analyzer/expandMacro')",
                },
                "customParams": {
                    "type": "object",
                    "description": "Method-specific parameters for customRequest",
                },
            },
            "required": ["operation", "file_path"],
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

        # Validate required parameters
        if not file_path:
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

        # Per-operation parameter validation
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

        # Detect language from file extension
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
            return ToolResult(
                success=False, error={"message": f"LSP operation failed: {e}"}
            )

    async def cleanup(self):
        """Shutdown all LSP servers."""
        await self._server_manager.shutdown_all()
