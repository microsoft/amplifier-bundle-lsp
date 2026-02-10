"""Tests for inlayHints operation (Task 6)."""

import pytest

from amplifier_module_tool_lsp.operations import LspOperations
from amplifier_module_tool_lsp.tool import LspTool


# ── Helpers ──────────────────────────────────────────────────────────────


class FakeInlayHintServer:
    """Fake LspServer that returns configurable inlay hint results."""

    def __init__(self, inlay_result=None):
        self.language = "python"
        self.notifications: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, dict]] = []
        self._inlay_result = inlay_result

    async def notify(self, method: str, params: dict):
        self.notifications.append((method, params))

    async def request(self, method: str, params: dict):
        self.requests.append((method, params))
        if method == "textDocument/inlayHint":
            return self._inlay_result
        return None


# ── Empty / null results ─────────────────────────────────────────────────


class TestInlayHintsEmptyResult:
    """inlayHints should handle empty/null results gracefully."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_null_result_returns_empty(self, ops, tmp_path):
        """Server returning None should produce empty hints list."""
        server = FakeInlayHintServer(inlay_result=None)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=10, end_character=1
        )

        assert result["hints"] == []
        assert result["count"] == 0
        assert "No hints" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self, ops, tmp_path):
        """Server returning [] should produce empty hints list."""
        server = FakeInlayHintServer(inlay_result=[])
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=10, end_character=1
        )

        assert result["hints"] == []
        assert result["count"] == 0
        assert "No hints" in result["message"]


# ── Type hints (kind=1) ─────────────────────────────────────────────────


class TestInlayHintsTypeHints:
    """inlayHints should format type hints (kind=1) correctly."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_type_hint_formatted(self, ops, tmp_path):
        """Type hint should have kind='type' and 1-based positions."""
        hints = [
            {"position": {"line": 0, "character": 5}, "label": ": i32", "kind": 1},
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("let x = 42;\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=2, end_character=1
        )

        assert result["count"] == 1
        hint = result["hints"][0]
        assert hint["line"] == 1  # 0-based → 1-based
        assert hint["character"] == 6  # 0-based → 1-based
        assert hint["label"] == ": i32"
        assert hint["kind"] == "type"

    @pytest.mark.asyncio
    async def test_type_hints_counted(self, ops, tmp_path):
        """Multiple type hints should be counted in type_hints and message."""
        hints = [
            {"position": {"line": 0, "character": 5}, "label": ": i32", "kind": 1},
            {"position": {"line": 1, "character": 5}, "label": ": String", "kind": 1},
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\ny = 'hi'\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=3, end_character=1
        )

        assert result["type_hints"] == 2
        assert result["parameter_hints"] == 0
        assert "2 type(s)" in result["message"]


# ── Parameter hints (kind=2) ────────────────────────────────────────────


class TestInlayHintsParameterHints:
    """inlayHints should format parameter hints (kind=2) correctly."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_parameter_hint(self, ops, tmp_path):
        """Parameter hint should have kind='parameter' and be counted."""
        hints = [
            {"position": {"line": 2, "character": 10}, "label": "name:", "kind": 2},
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("foo(bar)\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=5, end_character=1
        )

        assert result["count"] == 1
        assert result["hints"][0]["kind"] == "parameter"
        assert result["parameter_hints"] == 1
        assert "1 parameter name(s)" in result["message"]


# ── Mixed kinds ──────────────────────────────────────────────────────────


class TestInlayHintsMixedKinds:
    """inlayHints should count and report mixed hint kinds correctly."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_mixed_kinds_counted(self, ops, tmp_path):
        """Each kind should be counted separately and appear in message."""
        hints = [
            {"position": {"line": 0, "character": 5}, "label": ": i32", "kind": 1},
            {"position": {"line": 1, "character": 10}, "label": "name:", "kind": 2},
            {"position": {"line": 2, "character": 0}, "label": "'a", "kind": 3},
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("a\nb\nc\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=4, end_character=1
        )

        assert result["count"] == 3
        assert result["type_hints"] == 1
        assert result["parameter_hints"] == 1
        assert result["hints"][2]["kind"] == "other"
        assert "1 type(s)" in result["message"]
        assert "1 parameter name(s)" in result["message"]
        assert "1 other" in result["message"]

    @pytest.mark.asyncio
    async def test_unknown_kind_mapped_to_other(self, ops, tmp_path):
        """Kind values other than 1 or 2 should map to 'other'."""
        hints = [
            {"position": {"line": 0, "character": 0}, "label": "hint", "kind": 99},
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("x\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=2, end_character=1
        )

        assert result["hints"][0]["kind"] == "other"

    @pytest.mark.asyncio
    async def test_missing_kind_mapped_to_other(self, ops, tmp_path):
        """Missing kind field should map to 'other'."""
        hints = [
            {"position": {"line": 0, "character": 0}, "label": "hint"},
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("x\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=2, end_character=1
        )

        assert result["hints"][0]["kind"] == "other"


# ── Label formats ────────────────────────────────────────────────────────


class TestInlayHintsLabelFormats:
    """inlayHints should handle both string and InlayHintLabelPart[] labels."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_string_label(self, ops, tmp_path):
        """String labels should be used directly."""
        hints = [
            {
                "position": {"line": 0, "character": 5},
                "label": ": Vec<usize>",
                "kind": 1,
            },
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("let x = vec![1];\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=2, end_character=1
        )

        assert result["hints"][0]["label"] == ": Vec<usize>"

    @pytest.mark.asyncio
    async def test_label_parts_list(self, ops, tmp_path):
        """InlayHintLabelPart[] should be joined by .value fields."""
        hints = [
            {
                "position": {"line": 0, "character": 5},
                "label": [
                    {"value": ": "},
                    {"value": "Vec"},
                    {"value": "<"},
                    {"value": "usize"},
                    {"value": ">"},
                ],
                "kind": 1,
            },
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("let x = vec![1];\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=2, end_character=1
        )

        assert result["hints"][0]["label"] == ": Vec<usize>"

    @pytest.mark.asyncio
    async def test_empty_label_parts(self, ops, tmp_path):
        """Empty label parts list should produce empty string."""
        hints = [
            {
                "position": {"line": 0, "character": 0},
                "label": [],
                "kind": 1,
            },
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("x\n")

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=2, end_character=1
        )

        assert result["hints"][0]["label"] == ""


# ── Truncation at 100 ───────────────────────────────────────────────────


class TestInlayHintsTruncation:
    """inlayHints should cap at 100 hints to prevent context overflow."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_truncation_at_100(self, ops, tmp_path):
        """More than 100 hints should be truncated with metadata."""
        hints = [
            {"position": {"line": i, "character": 0}, "label": f": T{i}", "kind": 1}
            for i in range(150)
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("x\n" * 150)

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=200, end_character=1
        )

        assert result["count"] == 100
        assert len(result["hints"]) == 100
        assert result["truncated"] is True
        assert result["total_count"] == 150

    @pytest.mark.asyncio
    async def test_no_truncation_under_100(self, ops, tmp_path):
        """Fewer than 100 hints should not be truncated."""
        hints = [
            {"position": {"line": i, "character": 0}, "label": f": T{i}", "kind": 1}
            for i in range(50)
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("x\n" * 50)

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=60, end_character=1
        )

        assert result["count"] == 50
        assert result["truncated"] is False
        assert result["total_count"] == 50

    @pytest.mark.asyncio
    async def test_exactly_100_not_truncated(self, ops, tmp_path):
        """Exactly 100 hints should not be truncated."""
        hints = [
            {"position": {"line": i, "character": 0}, "label": f": T{i}", "kind": 1}
            for i in range(100)
        ]
        server = FakeInlayHintServer(inlay_result=hints)
        py_file = tmp_path / "test.py"
        py_file.write_text("x\n" * 100)

        result = await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=110, end_character=1
        )

        assert result["count"] == 100
        assert result["truncated"] is False
        assert result["total_count"] == 100


# ── Default end_line / end_character ─────────────────────────────────────


class TestInlayHintsDefaults:
    """inlayHints should default end_line to line+50 and end_character to 1."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_default_end_line(self, ops, tmp_path):
        """When end_line not provided, default to line+50."""
        server = FakeInlayHintServer(inlay_result=[])
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._op_inlayHints(server, str(py_file), 10, 1, None)

        assert len(server.requests) == 1
        _method, params = server.requests[0]
        # end_line defaults to 10+50=60, converted to 0-based = 59
        assert params["range"]["end"]["line"] == 59

    @pytest.mark.asyncio
    async def test_default_end_character(self, ops, tmp_path):
        """When end_character not provided, default to 1."""
        server = FakeInlayHintServer(inlay_result=[])
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._op_inlayHints(server, str(py_file), 10, 1, None)

        _method, params = server.requests[0]
        # end_character defaults to 1, converted to 0-based = 0
        assert params["range"]["end"]["character"] == 0

    @pytest.mark.asyncio
    async def test_explicit_end_line_used(self, ops, tmp_path):
        """When end_line is explicitly provided, use it."""
        server = FakeInlayHintServer(inlay_result=[])
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._op_inlayHints(
            server, str(py_file), 10, 1, None, end_line=20, end_character=5
        )

        _method, params = server.requests[0]
        assert params["range"]["end"]["line"] == 19  # 20 → 0-based
        assert params["range"]["end"]["character"] == 4  # 5 → 0-based

    @pytest.mark.asyncio
    async def test_range_start_uses_line_character(self, ops, tmp_path):
        """Range start should use line/character converted to 0-based."""
        server = FakeInlayHintServer(inlay_result=[])
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._op_inlayHints(
            server, str(py_file), 5, 3, None, end_line=10, end_character=1
        )

        _method, params = server.requests[0]
        assert params["range"]["start"]["line"] == 4  # 5 → 0-based
        assert params["range"]["start"]["character"] == 2  # 3 → 0-based

    @pytest.mark.asyncio
    async def test_request_uses_correct_uri(self, ops, tmp_path):
        """Request should use file URI for textDocument."""
        server = FakeInlayHintServer(inlay_result=[])
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._op_inlayHints(
            server, str(py_file), 1, 1, None, end_line=10, end_character=1
        )

        _method, params = server.requests[0]
        assert params["textDocument"]["uri"].startswith("file://")
        assert params["textDocument"]["uri"].endswith("test.py")


# ── Tool-level integration: end_line/end_character forwarding ─────────


class TestInlayHintsToolForwarding:
    """tool.execute() should forward end_line/end_character to the operation."""

    @pytest.mark.asyncio
    async def test_tool_forwards_end_line_end_character(self, tmp_path):
        """end_line/end_character from arguments should reach the LSP request."""
        tool = LspTool(
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

        # Set up fake server to capture requests
        fake_server = FakeInlayHintServer(inlay_result=[])

        async def mock_get_server(**kwargs):  # type: ignore[override]
            return fake_server

        tool._server_manager.get_server = mock_get_server  # type: ignore[assignment]

        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await tool.execute(
            {
                "operation": "inlayHints",
                "file_path": str(py_file),
                "line": 5,
                "character": 1,
                "end_line": 20,
                "end_character": 10,
            }
        )

        # Find the inlayHint request (skip didOpen notification)
        inlay_requests = [
            (m, p) for m, p in fake_server.requests if m == "textDocument/inlayHint"
        ]
        assert len(inlay_requests) == 1
        _method, params = inlay_requests[0]

        # end_line=20 → 0-based=19, end_character=10 → 0-based=9
        assert params["range"]["end"]["line"] == 19
        assert params["range"]["end"]["character"] == 9
