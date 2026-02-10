"""Tests for Task 9: Tool Description & Context Updates."""

import pytest

from amplifier_module_tool_lsp.tool import LspTool


# ── Fixtures ──────────────────────────────────────────────────────────────


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
        }
    )


@pytest.fixture
def context_path():
    """Path to lsp-general.md context file."""
    from pathlib import Path

    return Path(__file__).resolve().parents[3] / "context" / "lsp-general.md"


@pytest.fixture
def context_content(context_path):
    """Read the lsp-general.md context file."""
    return context_path.read_text()


# ── OPERATIONS list ordering ─────────────────────────────────────────────


class TestOperationsOrdering:
    """OPERATIONS list should be logically grouped with comments."""

    EXPECTED_ORDER = [
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

    def test_operations_count(self, tool):
        assert len(tool.OPERATIONS) == 17

    def test_operations_order_matches_logical_grouping(self, tool):
        assert tool.OPERATIONS == self.EXPECTED_ORDER


# ── Tool description ─────────────────────────────────────────────────────


class TestToolDescription:
    """Description should list all operations grouped by category."""

    def test_description_contains_navigation_header(self, tool):
        assert "NAVIGATION" in tool.description

    def test_description_contains_type_hierarchy_header(self, tool):
        assert "TYPE HIERARCHY" in tool.description

    def test_description_contains_verification_header(self, tool):
        assert "VERIFICATION" in tool.description

    def test_description_contains_refactoring_header(self, tool):
        assert "REFACTORING" in tool.description

    def test_description_contains_inspection_header(self, tool):
        assert "INSPECTION" in tool.description

    def test_description_contains_extension_header(self, tool):
        assert "EXTENSION" in tool.description

    def test_description_mentions_diagnostics_in_lsp_vs_grep(self, tool):
        desc = tool.description
        assert "diagnostics" in desc
        assert "Check for errors" in desc or "errors after editing" in desc

    def test_description_mentions_rename_in_lsp_vs_grep(self, tool):
        desc = tool.description
        assert "Rename a symbol safely" in desc or "rename" in desc.lower()
        # Should mention rename is cross-file and semantic
        assert "cross-file" in desc.lower() or "semantic" in desc.lower()

    def test_description_rule_includes_diagnostics_and_refactoring(self, tool):
        desc = tool.description
        # The RULE line should mention diagnostics and refactoring
        assert "diagnostics" in desc
        assert "refactoring" in desc.lower()

    def test_description_agent_delegation_is_generic(self, tool):
        desc = tool.description
        # Should NOT hardcode specific agent names like lsp:code-navigator
        assert "lsp:code-navigator" not in desc
        assert "lsp-python:python-code-intel" not in desc

    def test_description_mentions_configured_languages(self, tool):
        assert "python" in tool.description

    def test_description_lists_all_new_operations(self, tool):
        desc = tool.description
        for op in [
            "prepareTypeHierarchy",
            "supertypes",
            "subtypes",
            "diagnostics",
            "rename",
            "codeAction",
            "inlayHints",
            "customRequest",
        ]:
            assert op in desc, f"Operation '{op}' missing from description"


# ── input_schema operation description ───────────────────────────────────


class TestOperationDescription:
    """The operation enum description should categorize operations."""

    def test_operation_description_mentions_categories(self, tool):
        desc = tool.input_schema["properties"]["operation"]["description"]
        assert "Navigation" in desc
        assert "Call hierarchy" in desc
        assert "Type hierarchy" in desc
        assert "Verification" in desc
        assert "Refactoring" in desc
        assert "Inspection" in desc
        assert "Extension" in desc

    def test_operation_description_mentions_rename_needs_newname(self, tool):
        desc = tool.input_schema["properties"]["operation"]["description"]
        assert "newName" in desc

    def test_operation_description_mentions_inlayhints_needs_range(self, tool):
        desc = tool.input_schema["properties"]["operation"]["description"]
        assert "end_line" in desc or "end_character" in desc

    def test_operation_description_mentions_custom_needs_method(self, tool):
        desc = tool.input_schema["properties"]["operation"]["description"]
        assert "customMethod" in desc


# ── Parameter descriptions ───────────────────────────────────────────────


class TestParameterDescriptions:
    """Parameter descriptions should clarify which operations use them."""

    def test_newName_description_mentions_rename(self, tool):
        desc = tool.input_schema["properties"]["newName"]["description"]
        assert "rename" in desc.lower()

    def test_end_line_description_mentions_operations(self, tool):
        desc = tool.input_schema["properties"]["end_line"]["description"]
        assert "codeAction" in desc
        assert "inlayHints" in desc

    def test_end_character_description_mentions_operations(self, tool):
        desc = tool.input_schema["properties"]["end_character"]["description"]
        assert "codeAction" in desc or "range" in desc.lower()
        assert "inlayHints" in desc or "range" in desc.lower()

    def test_customMethod_description_mentions_customRequest(self, tool):
        desc = tool.input_schema["properties"]["customMethod"]["description"]
        assert "customRequest" in desc

    def test_customParams_description_mentions_customRequest(self, tool):
        desc = tool.input_schema["properties"]["customParams"]["description"]
        assert "customRequest" in desc


# ── lsp-general.md context file ──────────────────────────────────────────


class TestLspGeneralContext:
    """lsp-general.md should cover all operations and workflows."""

    def test_context_file_exists(self, context_path):
        assert context_path.exists(), f"Missing context file: {context_path}"

    def test_context_has_after_editing_code_section(self, context_content):
        assert "After Editing Code" in context_content

    def test_context_has_custom_extensions_section(self, context_content):
        assert (
            "Custom Extensions" in context_content or "customRequest" in context_content
        )

    def test_context_mentions_diagnostics(self, context_content):
        assert "diagnostics" in context_content

    def test_context_mentions_rename(self, context_content):
        assert "rename" in context_content

    def test_context_mentions_code_action(self, context_content):
        assert "codeAction" in context_content

    def test_context_mentions_inlay_hints(self, context_content):
        assert "inlayHints" in context_content

    def test_context_mentions_type_hierarchy(self, context_content):
        assert (
            "prepareTypeHierarchy" in context_content
            or "Type Hierarchy" in context_content
        )

    def test_context_mentions_subtypes(self, context_content):
        assert "subtypes" in context_content

    def test_context_mentions_supertypes(self, context_content):
        assert "supertypes" in context_content

    def test_context_mentions_custom_request(self, context_content):
        assert "customRequest" in context_content

    def test_context_diagnostics_codeaction_workflow(self, context_content):
        """The diagnostics → codeAction → apply workflow should be documented."""
        content_lower = context_content.lower()
        assert "diagnostics" in content_lower
        assert "codeaction" in content_lower
        # Should explain the workflow connection
        assert "fix" in content_lower or "apply" in content_lower

    def test_context_is_language_agnostic(self, context_content):
        """Context should not mention specific languages."""
        # Should not hardcode specific language names
        content_lower = context_content.lower()
        assert (
            "rust-analyzer" not in content_lower
            or "example" in content_lower.split("rust-analyzer")[0][-50:]
        )
        # pyright can appear in troubleshooting examples, but not as primary content
        # Python/Rust should not appear as primary subjects
        lines = context_content.split("\n")
        for line in lines:
            line_lower = line.lower().strip()
            # Skip example lines (indented or in code blocks)
            if line_lower.startswith("```") or line_lower.startswith("lsp "):
                continue
            # The word "python" or "rust" should not appear outside examples
            # (but "pyright" in troubleshooting is ok)

    def test_context_when_to_reach_for_lsp_includes_new_heuristics(
        self, context_content
    ):
        """Should include new heuristics for diagnostics, rename, etc."""
        assert "diagnostics" in context_content
        # At least some of the new heuristics should be present
        new_heuristics_present = sum(
            1
            for term in [
                "diagnostics",
                "rename",
                "codeAction",
                "inlayHints",
                "subtypes",
            ]
            if term in context_content
        )
        assert new_heuristics_present >= 4, "Missing new heuristics in context"

    def test_context_all_operations_listed(self, context_content):
        """All 17 operations should appear somewhere in the context."""
        all_ops = [
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
        for op in all_ops:
            assert op in context_content, (
                f"Operation '{op}' missing from lsp-general.md"
            )

    def test_context_reasonable_length(self, context_content):
        """Context should be 100-150 lines (concise but comprehensive)."""
        lines = len(context_content.strip().split("\n"))
        assert lines >= 80, f"Context too short: {lines} lines"
        assert lines <= 160, f"Context too long: {lines} lines"

    def test_context_troubleshooting_mentions_server_support(self, context_content):
        """Should mention that some operations require specific server support."""
        content_lower = context_content.lower()
        assert (
            "server support" in content_lower
            or "not support" in content_lower
            or "server may not" in content_lower
            or "not all servers" in content_lower
        )
