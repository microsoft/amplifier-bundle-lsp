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

    # Supported operations (from Claude Code's LSP tool)
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
    ]

    def __init__(self, config: dict):
        """Initialize with configuration containing language definitions."""
        self._languages = config.get("languages", {})
        self._timeout = config.get("timeout_seconds", 30)
        self._max_retries = config.get("max_retries", 3)
        self._max_results = config.get("max_results", 50)  # Prevent context overflow
        self._max_hover_chars = config.get("max_hover_chars", 6000)  # Limit hover content size
        self._server_manager = LspServerManager()
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
            f"Operations: {', '.join(self.OPERATIONS)}"
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
            },
            "required": ["operation", "file_path", "line", "character"],
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
        line = arguments.get("line", 1)
        character = arguments.get("character", 1)

        # Validate required parameters
        if not file_path:
            return ToolResult(
                success=False,
                error={"message": "file_path is required"},
            )

        # Validate operation
        if operation not in self.OPERATIONS:
            return ToolResult(
                success=False,
                error={"message": f"Unknown operation: {operation}. Valid: {self.OPERATIONS}"},
            )

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
                error={"message": f"Failed to start {language} LSP server: {e}. {install_hint}"},
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
            )
            return ToolResult(success=True, output=result)
        except Exception as e:
            return ToolResult(success=False, error={"message": f"LSP operation failed: {e}"})

    async def cleanup(self):
        """Shutdown all LSP servers."""
        await self._server_manager.shutdown_all()
