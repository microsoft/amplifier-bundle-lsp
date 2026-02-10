"""Tests for Task 5: Code Actions operation."""

import pytest

from amplifier_module_tool_lsp.operations import LspOperations
from amplifier_module_tool_lsp.tool import LspTool


# ── Helpers ──────────────────────────────────────────────────────────────


class FakeCodeActionServer:
    """Fake server for code action tests with configurable results and diagnostics cache."""

    def __init__(self, code_action_result=None, cached_diagnostics=None):
        self.language = "rust"
        self.notifications: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, dict]] = []
        self._code_action_result = code_action_result
        self._cached_diagnostics = cached_diagnostics or {}

    async def notify(self, method: str, params: dict):
        self.notifications.append((method, params))

    async def request(self, method: str, params: dict):
        self.requests.append((method, params))
        if method == "textDocument/codeAction":
            return self._code_action_result
        return None

    def get_cached_diagnostics(self, uri: str):
        return self._cached_diagnostics.get(uri)


# ── _diagnostic_in_range ─────────────────────────────────────────────────


class TestDiagnosticInRange:
    """_diagnostic_in_range should check if diagnostic overlaps with range."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_same_line_overlap(self, ops):
        """Diagnostic on same line as range should overlap."""
        diag = {"range": {"start": {"line": 2}, "end": {"line": 2}}}
        start = {"line": 2}
        end = {"line": 2}
        assert ops._diagnostic_in_range(diag, start, end) is True

    def test_no_overlap_diagnostic_after_range(self, ops):
        """Diagnostic starting after range ends should not overlap."""
        diag = {"range": {"start": {"line": 5}, "end": {"line": 6}}}
        start = {"line": 1}
        end = {"line": 3}
        assert ops._diagnostic_in_range(diag, start, end) is False

    def test_no_overlap_diagnostic_before_range(self, ops):
        """Diagnostic ending before range starts should not overlap."""
        diag = {"range": {"start": {"line": 0}, "end": {"line": 1}}}
        start = {"line": 3}
        end = {"line": 5}
        assert ops._diagnostic_in_range(diag, start, end) is False

    def test_multi_line_diagnostic_spanning_range(self, ops):
        """Diagnostic spanning the range should overlap."""
        diag = {"range": {"start": {"line": 2}, "end": {"line": 5}}}
        start = {"line": 3}
        end = {"line": 4}
        assert ops._diagnostic_in_range(diag, start, end) is True

    def test_point_range_on_diagnostic_line(self, ops):
        """Point range on a diagnostic line should overlap."""
        diag = {"range": {"start": {"line": 3}, "end": {"line": 3}}}
        start = {"line": 3}
        end = {"line": 3}
        assert ops._diagnostic_in_range(diag, start, end) is True


# ── _op_codeAction: empty results ────────────────────────────────────────


class TestCodeActionEmpty:
    """_op_codeAction should handle empty/null results gracefully."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_null_result_returns_empty(self, ops, tmp_path):
        """Server returning None should produce empty actions list."""
        server = FakeCodeActionServer(code_action_result=None)
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        assert result["actions"] == []
        assert result["count"] == 0
        assert "No actions" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self, ops, tmp_path):
        """Server returning [] should produce empty actions list."""
        server = FakeCodeActionServer(code_action_result=[])
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        assert result["actions"] == []
        assert result["count"] == 0
        assert "No actions" in result["message"]


# ── _op_codeAction: actions with edits ───────────────────────────────────


class TestCodeActionWithEdits:
    """Actions with 'edit' field should include formatted workspace edit."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_action_with_edit_formatted(self, ops, tmp_path):
        """Action with edit should have edit formatted by _format_workspace_edit."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        action = {
            "title": "Add missing import",
            "kind": "quickfix",
            "isPreferred": True,
            "edit": {
                "changes": {
                    rs_file.resolve().as_uri(): [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 0},
                            },
                            "newText": "use std::fmt::Debug;\n",
                        }
                    ]
                }
            },
        }
        server = FakeCodeActionServer(code_action_result=[action])

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        assert result["count"] == 1
        entry = result["actions"][0]
        assert entry["title"] == "Add missing import"
        assert entry["kind"] == "quickfix"
        assert entry["is_preferred"] is True
        assert "edit" in entry
        assert entry["edit"]["description"] == "Add missing import"
        assert entry["edit"]["total_edits"] == 1
        assert entry["edit"]["edits"][0]["new_text"] == "use std::fmt::Debug;\n"


# ── _op_codeAction: actions with commands ────────────────────────────────


class TestCodeActionWithCommands:
    """Actions with command but no edit should note has_command."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_action_with_command_only(self, ops, tmp_path):
        """Command-only action should have has_command=True and command_title."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        action = {
            "title": "Extract into function",
            "kind": "refactor.extract",
            "command": {
                "title": "Apply refactoring",
                "command": "rust-analyzer.applyActionGroup",
                "arguments": [],
            },
        }
        server = FakeCodeActionServer(code_action_result=[action])

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        entry = result["actions"][0]
        assert entry["title"] == "Extract into function"
        assert entry["has_command"] is True
        assert entry["command_title"] == "Apply refactoring"
        assert "edit" not in entry

    @pytest.mark.asyncio
    async def test_action_with_both_edit_and_command(self, ops, tmp_path):
        """Action with both edit and command should include edit but not has_command."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        action = {
            "title": "Fix import",
            "kind": "quickfix",
            "edit": {
                "changes": {
                    "file:///test.rs": [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 0},
                            },
                            "newText": "use std::io;\n",
                        }
                    ]
                }
            },
            "command": {
                "title": "Apply fix",
                "command": "some.command",
            },
        }
        server = FakeCodeActionServer(code_action_result=[action])

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        entry = result["actions"][0]
        assert "edit" in entry
        assert "has_command" not in entry  # command is suppressed when edit present


# ── _op_codeAction: sorting ──────────────────────────────────────────────


class TestCodeActionSorting:
    """Actions should be sorted: preferred first, then quickfix, then others."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_preferred_sorted_first(self, ops, tmp_path):
        """isPreferred=True actions should appear before non-preferred."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        actions = [
            {"title": "Non-preferred", "kind": "quickfix", "isPreferred": False},
            {"title": "Preferred", "kind": "quickfix", "isPreferred": True},
        ]
        server = FakeCodeActionServer(code_action_result=actions)

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        assert result["actions"][0]["title"] == "Preferred"
        assert result["actions"][1]["title"] == "Non-preferred"

    @pytest.mark.asyncio
    async def test_quickfix_sorted_before_refactoring(self, ops, tmp_path):
        """quickfix kind should sort before refactoring kind (both non-preferred)."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        actions = [
            {"title": "Refactoring", "kind": "refactor.extract"},
            {"title": "Quick Fix", "kind": "quickfix"},
        ]
        server = FakeCodeActionServer(code_action_result=actions)

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        assert result["actions"][0]["title"] == "Quick Fix"
        assert result["actions"][1]["title"] == "Refactoring"


# ── _op_codeAction: range handling ───────────────────────────────────────


class TestCodeActionRange:
    """codeAction should handle point ranges and explicit ranges."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_point_range_when_no_end_params(self, ops, tmp_path):
        """Without end_line/end_character, should use point range (start == end)."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        server = FakeCodeActionServer(code_action_result=[])

        await ops._op_codeAction(server, str(rs_file), 5, 3, None)

        # Check the request params
        method, params = server.requests[0]
        assert method == "textDocument/codeAction"
        assert params["range"]["start"] == {"line": 4, "character": 2}
        assert params["range"]["end"] == {"line": 4, "character": 2}

    @pytest.mark.asyncio
    async def test_explicit_range_with_end_params(self, ops, tmp_path):
        """With end_line/end_character, should use explicit range."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {\n    let x = 1;\n}\n")

        server = FakeCodeActionServer(code_action_result=[])

        await ops._op_codeAction(
            server, str(rs_file), 1, 1, None, end_line=3, end_character=2
        )

        method, params = server.requests[0]
        assert method == "textDocument/codeAction"
        assert params["range"]["start"] == {"line": 0, "character": 0}
        assert params["range"]["end"] == {"line": 2, "character": 1}


# ── _op_codeAction: diagnostic context ───────────────────────────────────


class TestCodeActionDiagnosticContext:
    """Cached diagnostics should be filtered and passed to server."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_relevant_diagnostics_passed(self, ops, tmp_path):
        """Diagnostics overlapping the range should be passed in context."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {\n    let x = 1;\n}\n")
        uri = rs_file.resolve().as_uri()

        overlapping_diag = {
            "range": {
                "start": {"line": 1, "character": 4},
                "end": {"line": 1, "character": 10},
            },
            "message": "unused variable",
            "severity": 2,
        }
        non_overlapping_diag = {
            "range": {
                "start": {"line": 5, "character": 0},
                "end": {"line": 5, "character": 5},
            },
            "message": "other error",
            "severity": 1,
        }

        server = FakeCodeActionServer(
            code_action_result=[],
            cached_diagnostics={uri: [overlapping_diag, non_overlapping_diag]},
        )

        # Request at line 2 (1-based) → 0-based line 1, matching the overlapping diag
        await ops._op_codeAction(server, str(rs_file), 2, 5, None)

        method, params = server.requests[0]
        assert method == "textDocument/codeAction"
        context_diags = params["context"]["diagnostics"]
        assert len(context_diags) == 1
        assert context_diags[0]["message"] == "unused variable"
        assert params["context"]["triggerKind"] == 1

    @pytest.mark.asyncio
    async def test_no_cached_diagnostics(self, ops, tmp_path):
        """No cached diagnostics should pass empty list in context."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        server = FakeCodeActionServer(code_action_result=[])

        await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        _, params = server.requests[0]
        assert params["context"]["diagnostics"] == []


# ── _op_codeAction: truncation ───────────────────────────────────────────


class TestCodeActionTruncation:
    """Actions should be capped at 30 to prevent context overflow."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_truncation_at_30(self, ops, tmp_path):
        """More than 30 actions should be truncated with metadata."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        actions = [{"title": f"Action {i}", "kind": "refactor"} for i in range(40)]
        server = FakeCodeActionServer(code_action_result=actions)

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        assert result["count"] == 30
        assert len(result["actions"]) == 30
        assert result["truncated"] is True
        assert result["total_count"] == 40

    @pytest.mark.asyncio
    async def test_no_truncation_under_30(self, ops, tmp_path):
        """Fewer than 30 actions should not be truncated."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        actions = [{"title": f"Action {i}", "kind": "quickfix"} for i in range(10)]
        server = FakeCodeActionServer(code_action_result=actions)

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        assert result["count"] == 10
        assert result["truncated"] is False
        assert result["total_count"] == 10


# ── _op_codeAction: fixes_diagnostics ────────────────────────────────────


class TestCodeActionFixesDiagnostics:
    """Actions with diagnostics should report fixes_diagnostics count."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_fixes_diagnostics_count(self, ops, tmp_path):
        """Action with diagnostics should have fixes_diagnostics count."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        action = {
            "title": "Remove unused import",
            "kind": "quickfix",
            "diagnostics": [
                {"message": "unused import", "severity": 2},
                {"message": "also unused", "severity": 2},
            ],
        }
        server = FakeCodeActionServer(code_action_result=[action])

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        entry = result["actions"][0]
        assert entry["fixes_diagnostics"] == 2

    @pytest.mark.asyncio
    async def test_no_fixes_diagnostics_when_absent(self, ops, tmp_path):
        """Action without diagnostics should not have fixes_diagnostics key."""
        rs_file = tmp_path / "main.rs"
        rs_file.write_text("fn main() {}\n")

        action = {
            "title": "Extract function",
            "kind": "refactor.extract",
        }
        server = FakeCodeActionServer(code_action_result=[action])

        result = await ops._op_codeAction(server, str(rs_file), 1, 1, None)

        entry = result["actions"][0]
        assert "fixes_diagnostics" not in entry


# ── tool.py: codeAction validation ───────────────────────────────────────


class TestCodeActionToolValidation:
    """codeAction should not be in _NOT_YET_IMPLEMENTED_OPS."""

    @pytest.fixture
    def tool(self):
        return LspTool(
            {
                "languages": {
                    "rust": {
                        "extensions": [".rs"],
                        "workspace_markers": ["Cargo.toml"],
                        "server": {"command": ["rust-analyzer"]},
                    }
                },
            }
        )

    def test_code_action_not_in_not_yet_implemented(self, tool):
        """codeAction should NOT be in _NOT_YET_IMPLEMENTED_OPS."""
        assert "codeAction" not in tool._NOT_YET_IMPLEMENTED_OPS
