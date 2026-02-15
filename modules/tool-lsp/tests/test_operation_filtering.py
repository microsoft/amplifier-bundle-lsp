"""Tests for dynamic operation filtering based on language capabilities."""

import pytest

from amplifier_module_tool_lsp.tool import LspTool


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def rust_like_tool():
    """Tool with Rust-like capabilities (no type hierarchy)."""
    return LspTool(
        {
            "languages": {
                "rust": {
                    "extensions": [".rs"],
                    "workspace_markers": ["Cargo.toml"],
                    "server": {"command": ["rust-analyzer"]},
                    "capabilities": {
                        "diagnostics": True,
                        "rename": True,
                        "codeAction": True,
                        "inlayHints": True,
                        "customRequest": True,
                        "goToImplementation": True,
                    },
                }
            }
        }
    )


@pytest.fixture
def no_caps_tool():
    """Tool with no capabilities key (backward compat — all ops shown)."""
    return LspTool(
        {
            "languages": {
                "python": {
                    "extensions": [".py"],
                    "workspace_markers": ["pyproject.toml"],
                    "server": {"command": ["pyright-langserver", "--stdio"]},
                }
            }
        }
    )


@pytest.fixture
def multi_lang_tool():
    """Tool with two languages, each declaring different capabilities."""
    return LspTool(
        {
            "languages": {
                "rust": {
                    "extensions": [".rs"],
                    "workspace_markers": ["Cargo.toml"],
                    "server": {"command": ["rust-analyzer"]},
                    "capabilities": {
                        "diagnostics": True,
                        "rename": True,
                    },
                },
                "go": {
                    "extensions": [".go"],
                    "workspace_markers": ["go.mod"],
                    "server": {"command": ["gopls"]},
                    "capabilities": {
                        "codeAction": True,
                        "inlayHints": True,
                    },
                },
            }
        }
    )


@pytest.fixture
def minimal_caps_tool():
    """Tool with only diagnostics capability declared."""
    return LspTool(
        {
            "languages": {
                "test": {
                    "extensions": [".test"],
                    "workspace_markers": [],
                    "server": {"command": ["test-ls"]},
                    "capabilities": {
                        "diagnostics": True,
                    },
                }
            }
        }
    )


# ── Base operations always available ──────────────────────────────────


class TestBaseOpsAlwaysAvailable:
    """Base 8 operations always appear regardless of capabilities config."""

    BASE_OPS = [
        "goToDefinition",
        "findReferences",
        "hover",
        "documentSymbol",
        "workspaceSymbol",
        "prepareCallHierarchy",
        "incomingCalls",
        "outgoingCalls",
    ]

    @pytest.mark.parametrize("op", BASE_OPS)
    def test_base_ops_in_schema_with_capabilities(self, rust_like_tool, op):
        enum = rust_like_tool.input_schema["properties"]["operation"]["enum"]
        assert op in enum

    @pytest.mark.parametrize("op", BASE_OPS)
    def test_base_ops_in_schema_with_minimal_caps(self, minimal_caps_tool, op):
        enum = minimal_caps_tool.input_schema["properties"]["operation"]["enum"]
        assert op in enum


# ── Extended operations filtered by capabilities ──────────────────────


class TestExtendedOpsFilteredByCapabilities:
    """Extended operations only appear if declared truthy in capabilities."""

    def test_enabled_extended_ops_present(self, rust_like_tool):
        enum = rust_like_tool.input_schema["properties"]["operation"]["enum"]
        for op in [
            "diagnostics",
            "rename",
            "codeAction",
            "inlayHints",
            "customRequest",
            "goToImplementation",
        ]:
            assert op in enum, f"{op} should be in enum when capability is true"

    def test_only_declared_ops_from_extended(self, minimal_caps_tool):
        enum = minimal_caps_tool.input_schema["properties"]["operation"]["enum"]
        assert "diagnostics" in enum
        for op in [
            "rename",
            "codeAction",
            "inlayHints",
            "customRequest",
            "goToImplementation",
        ]:
            assert op not in enum, (
                f"{op} should not appear with only diagnostics capability"
            )


# ── No capabilities shows all ─────────────────────────────────────────


class TestNoCapabilitiesShowsAll:
    """Language with no capabilities key → all operations shown (backward compat)."""

    def test_all_ops_shown_when_no_caps_key(self, no_caps_tool):
        enum = no_caps_tool.input_schema["properties"]["operation"]["enum"]
        assert enum == LspTool.OPERATIONS

    def test_no_languages_shows_all(self):
        tool = LspTool({"languages": {}})
        enum = tool.input_schema["properties"]["operation"]["enum"]
        assert enum == LspTool.OPERATIONS

    def test_mixed_caps_and_no_caps_shows_all(self):
        """If ANY language lacks capabilities, show all ops (backward compat)."""
        tool = LspTool(
            {
                "languages": {
                    "python": {
                        "extensions": [".py"],
                        "server": {"command": ["pyright"]},
                        # no capabilities key
                    },
                    "rust": {
                        "extensions": [".rs"],
                        "server": {"command": ["rust-analyzer"]},
                        "capabilities": {"diagnostics": True},
                    },
                }
            }
        )
        enum = tool.input_schema["properties"]["operation"]["enum"]
        assert enum == LspTool.OPERATIONS


# ── Multi-language union ──────────────────────────────────────────────


class TestMultiLanguageUnion:
    """Show operation if ANY configured language supports it (union)."""

    def test_union_of_capabilities(self, multi_lang_tool):
        enum = multi_lang_tool.input_schema["properties"]["operation"]["enum"]
        # From rust: diagnostics, rename
        assert "diagnostics" in enum
        assert "rename" in enum
        # From go: codeAction, inlayHints
        assert "codeAction" in enum
        assert "inlayHints" in enum

    def test_undeclared_in_all_still_hidden(self, multi_lang_tool):
        enum = multi_lang_tool.input_schema["properties"]["operation"]["enum"]
        # Neither language declares these
        for op in ["customRequest", "goToImplementation"]:
            assert op not in enum, (
                f"{op} not declared by any language, should be hidden"
            )


# ── Description excludes hidden ops ───────────────────────────────────


class TestDescriptionExcludesHiddenOps:
    """Description text should not mention operations that are hidden."""

    def test_has_sections_for_enabled_ops(self, rust_like_tool):
        desc = rust_like_tool.description
        assert "VERIFICATION" in desc
        assert "REFACTORING" in desc
        assert "INSPECTION" in desc
        assert "EXTENSION" in desc
        assert "diagnostics" in desc
        assert "rename" in desc
        assert "codeAction" in desc

    def test_minimal_caps_hides_most_sections(self, minimal_caps_tool):
        desc = minimal_caps_tool.description
        assert "VERIFICATION" in desc  # diagnostics is enabled
        assert "REFACTORING" not in desc  # rename/codeAction not enabled
        assert "INSPECTION" not in desc  # inlayHints not enabled
        assert "EXTENSION" not in desc  # customRequest not enabled

    def test_schema_description_excludes_hidden_categories(self, rust_like_tool):
        desc = rust_like_tool.input_schema["properties"]["operation"]["description"]
        assert "Type hierarchy" not in desc

    def test_gotoimplementation_hidden_when_not_declared(self, minimal_caps_tool):
        desc = minimal_caps_tool.description
        assert "goToImplementation" not in desc

    def test_gotoimplementation_shown_when_declared(self, rust_like_tool):
        desc = rust_like_tool.description
        assert "goToImplementation" in desc


# ── Execute rejects unavailable operation ─────────────────────────────


class TestExecuteRejectsUnavailableOp:
    """Calling a hidden operation gives a helpful error message."""

    @pytest.mark.asyncio
    async def test_rejects_hidden_op(self, minimal_caps_tool, tmp_path):
        test_file = tmp_path / "test.test"
        test_file.write_text("x = 1\n")
        result = await minimal_caps_tool.execute(
            {
                "operation": "customRequest",
                "file_path": str(test_file),
                "customMethod": "test/method",
            }
        )
        assert result.success is False
        msg = result.error["message"].lower()
        assert "not supported" in msg

    @pytest.mark.asyncio
    async def test_rejects_with_available_list(self, minimal_caps_tool, tmp_path):
        test_file = tmp_path / "test.test"
        test_file.write_text("x = 1\n")
        result = await minimal_caps_tool.execute(
            {
                "operation": "rename",
                "file_path": str(test_file),
                "line": 1,
                "character": 1,
                "newName": "y",
            }
        )
        assert result.success is False
        assert (
            "Available" in result.error["message"]
            or "available" in result.error["message"].lower()
        )

    @pytest.mark.asyncio
    async def test_unknown_op_still_rejected(self, rust_like_tool, tmp_path):
        rs_file = tmp_path / "test.rs"
        rs_file.write_text("fn main() {}\n")
        result = await rust_like_tool.execute(
            {
                "operation": "totallyBogusOp",
                "file_path": str(rs_file),
                "line": 1,
                "character": 4,
            }
        )
        assert result.success is False
        assert "Unknown operation" in result.error["message"]
