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

    # Base operations - universally supported by all LSP servers, always shown
    BASE_OPERATIONS = [
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

    # Extended operations that require capability declaration.
    # If a language config has no capabilities map at all, all are assumed supported.
    EXTENDED_OPS = {
        "diagnostics",
        "rename",
        "codeAction",
        "inlayHints",
        "customRequest",
    }

    # Operations that require line/character position params
    POSITION_REQUIRED_OPS = {
        "goToDefinition",
        "findReferences",
        "hover",
        "goToImplementation",
        "prepareCallHierarchy",
        "incomingCalls",
        "outgoingCalls",
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

    def _get_available_operations(self) -> list[str]:
        """Get operations available based on configured language capabilities.

        Base operations (9) are always available. Extended operations only appear
        if at least one configured language declares them in its capabilities map.
        If a language has no capabilities map, all operations are assumed available.
        """
        if not self._languages:
            return list(self.OPERATIONS)  # No languages configured, show all

        # Collect union of all capabilities across configured languages
        available_extended: set[str] = set()
        has_any_capabilities_config = False

        for lang_config in self._languages.values():
            caps = lang_config.get("capabilities")
            if caps is None:
                # No capabilities map = assume all supported (backward compatible)
                return list(self.OPERATIONS)
            has_any_capabilities_config = True
            for op_name, enabled in caps.items():
                if enabled:
                    available_extended.add(op_name)

        if not has_any_capabilities_config:
            return list(self.OPERATIONS)

        # Build filtered list: base ops + enabled extended ops (preserving OPERATIONS order)
        result = list(self.BASE_OPERATIONS)
        for op in self.OPERATIONS:
            if op in self.EXTENDED_OPS and op in available_extended:
                result.append(op)

        return result

    @property
    def name(self) -> str:
        return "LSP"

    @property
    def description(self) -> str:
        available = set(self._get_available_operations())
        lang_names = ", ".join(self._languages.keys()) if self._languages else "none"

        sections = [
            f"Interact with Language Server Protocol servers for code intelligence. "
            f"Configured languages: {lang_names}.",
            # Navigation — always present (base ops)
            "NAVIGATION operations:\n"
            "  goToDefinition, findReferences, hover, documentSymbol, workspaceSymbol,\n"
            "  goToImplementation, prepareCallHierarchy, incomingCalls, outgoingCalls",
        ]

        # Verification
        if "diagnostics" in available:
            sections.append(
                "VERIFICATION operations:\n"
                "  diagnostics - get compiler errors/warnings for a file"
            )

        # Refactoring
        refactor_lines = []
        if "rename" in available:
            refactor_lines.append(
                "  rename - cross-file semantic rename (returns edits, does not apply)"
            )
        if "codeAction" in available:
            refactor_lines.append(
                "  codeAction - suggested fixes and refactorings (returns edits)"
            )
        if refactor_lines:
            sections.append("REFACTORING operations:\n" + "\n".join(refactor_lines))

        # Inspection
        if "inlayHints" in available:
            sections.append(
                "INSPECTION operations:\n"
                "  inlayHints - inferred types/parameter names for a range"
            )

        # Extension
        if "customRequest" in available:
            sections.append(
                "EXTENSION operations:\n"
                "  customRequest - send any server-specific LSP method"
            )

        # LSP vs GREP guidance
        grep_lines = [
            "WHEN TO USE LSP vs GREP:",
            "- Find callers of a function: incomingCalls (semantic) vs grep (matches strings/comments)",
            "- Find where symbol is defined: goToDefinition (precise) vs grep (multiple matches)",
            "- Get type info or signature: hover (full type data) - grep cannot do this",
        ]
        if "diagnostics" in available:
            grep_lines.append(
                "- Check for errors after editing: diagnostics - grep cannot do this"
            )
        if "rename" in available:
            grep_lines.append(
                "- Rename a symbol safely: rename (cross-file, semantic) vs grep (text-only, unsafe)"
            )
        grep_lines.extend(
            [
                "- Find text pattern anywhere: use grep instead (faster for text search)",
                "- Search across many files: use grep instead (faster for bulk search)",
            ]
        )
        sections.append("\n".join(grep_lines))

        # RULE
        rule_parts = ["types", "references", "call chains"]
        if "diagnostics" in available:
            rule_parts.append("diagnostics")
        if "rename" in available or "codeAction" in available:
            rule_parts.append("refactoring")
        sections.append(
            f"RULE: Use LSP for semantic code understanding ({', '.join(rule_parts)}). "
            "Use grep for text pattern matching."
        )

        # Delegation
        sections.append(
            "For complex multi-step navigation tasks, delegate to the appropriate "
            "language-specific code-intel agent."
        )

        return "\n\n".join(sections)

    def _build_operation_description(self, available: set[str]) -> str:
        """Build the operation enum description based on available operations."""
        parts = [
            "The LSP operation to perform.",
            "Navigation: goToDefinition, findReferences, hover, documentSymbol, "
            "workspaceSymbol, goToImplementation.",
            "Call hierarchy: prepareCallHierarchy, incomingCalls, outgoingCalls.",
        ]

        if "diagnostics" in available:
            parts.append("Verification: diagnostics.")

        refactor = []
        if "rename" in available:
            refactor.append("rename (needs newName)")
        if "codeAction" in available:
            refactor.append("codeAction")
        if refactor:
            parts.append(f"Refactoring: {', '.join(refactor)}.")

        if "inlayHints" in available:
            parts.append("Inspection: inlayHints (needs end_line, end_character).")

        if "customRequest" in available:
            parts.append("Extension: customRequest (needs customMethod).")

        return " ".join(parts)

    @property
    def input_schema(self) -> dict:
        available = self._get_available_operations()
        available_set = set(available)
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": available,
                    "description": self._build_operation_description(available_set),
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
        """Find workspace root by looking for marker files.

        For Cargo workspaces, prefers the root Cargo.toml (containing [workspace])
        over crate-level Cargo.toml files. For .git, returns immediately since it
        always marks the project root.
        """
        path = Path(file_path).resolve()
        markers = lang_config.get("workspace_markers", [])

        # Walk up directory tree, collecting all marker matches.
        # For Cargo.toml specifically, keep walking to find the workspace root.
        best_match = None
        current = path.parent
        while current != current.parent:
            for marker in markers:
                if (current / marker).exists():
                    if best_match is None:
                        best_match = current
                    # For Cargo.toml, check if this is a workspace root
                    if marker == "Cargo.toml":
                        try:
                            content = (current / "Cargo.toml").read_text()
                            if "[workspace]" in content:
                                return current  # Found workspace root
                        except OSError:
                            pass
                    # For .git, this is always the project root
                    elif marker == ".git":
                        return current
            current = current.parent

        return best_match or path.parent

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
                    "message": f"Unknown operation: {operation}. Valid: {self._get_available_operations()}"
                },
            )

        # Check if operation is available for configured languages
        available = self._get_available_operations()
        if operation not in available:
            return ToolResult(
                success=False,
                error={
                    "message": f"Operation '{operation}' is not supported by the configured language server(s). Available: {available}"
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
