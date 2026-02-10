"""Tests for tool.py Task 1 changes."""

import pytest

from amplifier_module_tool_lsp.tool import LspTool


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tool():
    """Create an LspTool with minimal config."""
    return LspTool(
        {
            "languages": {
                "python": {
                    "extensions": [".py"],
                    "workspace_markers": ["pyproject.toml"],
                    "server": {"command": ["pyright-langserver", "--stdio"]},
                }
            },
            "timeout_seconds": 45,
        }
    )


# ── Step 1.2: line/character optional in schema ──────────────────────────────


class TestSchemaRequired:
    """line and character should NOT be in required list."""

    def test_required_only_operation_and_file_path(self, tool):
        schema = tool.input_schema
        assert schema["required"] == ["operation", "file_path"]

    def test_line_still_in_properties(self, tool):
        props = tool.input_schema["properties"]
        assert "line" in props

    def test_character_still_in_properties(self, tool):
        props = tool.input_schema["properties"]
        assert "character" in props


class TestPositionValidation:
    """Operations that need position params should validate them."""

    POSITION_REQUIRED_OPS = [
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
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("op", POSITION_REQUIRED_OPS)
    async def test_position_required_ops_error_without_line(self, tool, op, tmp_path):
        """Operations requiring position should return error when line missing."""
        # Create a real .py file so language detection works
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")
        result = await tool.execute(
            {
                "operation": op,
                "file_path": str(py_file),
                # no line or character
            }
        )
        assert result.success is False
        assert (
            "line" in result.error["message"].lower()
            or "position" in result.error["message"].lower()
        )

    NO_POSITION_OPS = ["documentSymbol", "workspaceSymbol", "diagnostics"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("op", NO_POSITION_OPS)
    async def test_no_position_ops_accepted_without_line(self, tool, op, tmp_path):
        """Operations not requiring position should NOT error about missing line."""
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")
        result = await tool.execute(
            {
                "operation": op,
                "file_path": str(py_file),
            }
        )
        # Should NOT fail with a "position required" error.
        # It may fail for other reasons (no server running) but that's fine.
        if not result.success:
            msg = result.error["message"].lower()
            assert "line" not in msg or "position" not in msg or "required" not in msg


# ── Step 1.3: New schema parameters ──────────────────────────────────────────


class TestNewSchemaParams:
    """New params should exist in schema properties."""

    NEW_PARAMS = [
        "newName",
        "end_line",
        "end_character",
        "customMethod",
        "customParams",
    ]

    @pytest.mark.parametrize("param", NEW_PARAMS)
    def test_new_param_in_properties(self, tool, param):
        props = tool.input_schema["properties"]
        assert param in props, f"Expected '{param}' in schema properties"

    @pytest.mark.parametrize("param", NEW_PARAMS)
    def test_new_params_not_required(self, tool, param):
        assert param not in tool.input_schema["required"]

    def test_newName_is_string(self, tool):
        assert tool.input_schema["properties"]["newName"]["type"] == "string"

    def test_end_line_is_integer_with_minimum(self, tool):
        prop = tool.input_schema["properties"]["end_line"]
        assert prop["type"] == "integer"
        assert prop["minimum"] == 1

    def test_end_character_is_integer_with_minimum(self, tool):
        prop = tool.input_schema["properties"]["end_character"]
        assert prop["type"] == "integer"
        assert prop["minimum"] == 1

    def test_customMethod_is_string(self, tool):
        assert tool.input_schema["properties"]["customMethod"]["type"] == "string"

    def test_customParams_is_object(self, tool):
        assert tool.input_schema["properties"]["customParams"]["type"] == "object"


# ── Step 1.5 (in tool.py): Add new operations ────────────────────────────────


class TestNewOperations:
    """New operations should appear in OPERATIONS list and schema enum."""

    NEW_OPS = [
        "prepareTypeHierarchy",
        "supertypes",
        "subtypes",
        "diagnostics",
        "rename",
        "codeAction",
        "inlayHints",
        "customRequest",
    ]

    ORIGINAL_OPS = [
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

    @pytest.mark.parametrize("op", NEW_OPS)
    def test_new_op_in_operations_list(self, tool, op):
        assert op in tool.OPERATIONS

    @pytest.mark.parametrize("op", NEW_OPS)
    def test_new_op_in_schema_enum(self, tool, op):
        enum = tool.input_schema["properties"]["operation"]["enum"]
        assert op in enum

    @pytest.mark.parametrize("op", ORIGINAL_OPS)
    def test_original_ops_preserved(self, tool, op):
        assert op in tool.OPERATIONS

    # customRequest, diagnostics, type hierarchy now implemented — only check the still-unimplemented ones
    STILL_UNIMPLEMENTED_OPS = [
        "codeAction",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("op", STILL_UNIMPLEMENTED_OPS)
    async def test_new_ops_return_not_implemented(self, tool, op, tmp_path):
        """Unimplemented ops should be accepted but return 'not yet implemented'."""
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")
        result = await tool.execute(
            {
                "operation": op,
                "file_path": str(py_file),
                "line": 1,
                "character": 1,
            }
        )
        assert result.success is False
        assert (
            "not yet implemented" in result.error["message"].lower()
            or "not implemented" in result.error["message"].lower()
        )


# ── Step 1.10: Remove dead max_retries config ────────────────────────────────


class TestRemoveMaxRetries:
    """max_retries config should no longer be stored."""

    def test_no_max_retries_attribute(self, tool):
        assert not hasattr(tool, "_max_retries")


# ── Step 1.1 (tool side): Wire timeout ───────────────────────────────────────


class TestTimeoutWiring:
    """Timeout should be stored and passed to server manager."""

    def test_timeout_stored(self, tool):
        assert tool._timeout == 45

    def test_timeout_default(self):
        t = LspTool({"languages": {}})
        assert t._timeout == 30

    def test_timeout_passed_to_server_manager(self, tool):
        """Server manager should receive the timeout value."""
        # The server manager should have the timeout available
        assert hasattr(tool._server_manager, "_timeout")
        assert tool._server_manager._timeout == 45


# ── Task 7: customRequest tool-level validation ─────────────────────────────


class TestCustomRequestValidation:
    """customRequest should validate customMethod and handle optional file_path."""

    @pytest.mark.asyncio
    async def test_custom_request_not_in_unimplemented(self, tool):
        """customRequest should NOT be in _NOT_YET_IMPLEMENTED_OPS."""
        assert "customRequest" not in tool._NOT_YET_IMPLEMENTED_OPS

    @pytest.mark.asyncio
    async def test_missing_custom_method_returns_error(self, tool, tmp_path):
        """customRequest without customMethod should return clear error."""
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")
        result = await tool.execute(
            {
                "operation": "customRequest",
                "file_path": str(py_file),
            }
        )
        assert result.success is False
        assert "customMethod" in result.output or "customMethod" in str(result.error)

    @pytest.mark.asyncio
    async def test_no_file_path_accepted(self, tool):
        """customRequest should accept calls without file_path."""
        # It should NOT fail with "file_path is required" error.
        # It may fail trying to start server, but that's a different error.
        result = await tool.execute(
            {
                "operation": "customRequest",
                "customMethod": "test/method",
            }
        )
        # Should NOT get "file_path is required" error
        if not result.success and result.error:
            assert "file_path is required" not in result.error.get("message", "")

    @pytest.mark.asyncio
    async def test_no_file_path_no_languages_returns_error(self):
        """customRequest without file_path and no languages configured → error."""
        empty_tool = LspTool({"languages": {}})
        result = await empty_tool.execute(
            {
                "operation": "customRequest",
                "customMethod": "test/method",
            }
        )
        assert result.success is False
        combined = (str(result.output) + str(result.error)).lower()
        assert "no languages" in combined

    @pytest.mark.asyncio
    async def test_no_file_path_uses_first_language(self, tool):
        """customRequest without file_path should use first configured language."""
        # This will fail when trying to start the server (no real LSP),
        # but should get past file_path validation and language detection.
        result = await tool.execute(
            {
                "operation": "customRequest",
                "customMethod": "test/method",
            }
        )
        # Should NOT fail with language detection or file_path errors
        if not result.success and result.error:
            msg = result.error.get("message", "")
            assert "file_path is required" not in msg
            assert "No LSP support configured" not in msg
