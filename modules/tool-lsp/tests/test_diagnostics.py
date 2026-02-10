"""Tests for Task 3: Diagnostics (Pull + Push)."""

from unittest.mock import AsyncMock, patch

import pytest

from amplifier_module_tool_lsp.operations import LspOperations


# ── Helpers ──────────────────────────────────────────────────────────────────


class FakeDiagnosticsServer:
    """Fake LspServer for diagnostics tests.

    Supports configurable pull response and push cache.
    """

    def __init__(
        self,
        pull_response=None,
        pull_raises=None,
        cached_diagnostics=None,
    ):
        self.language = "rust"
        self.notifications: list[tuple[str, dict]] = []
        self._pull_response = pull_response
        self._pull_raises = pull_raises
        self._cached_diagnostics = cached_diagnostics

    async def notify(self, method: str, params: dict):
        self.notifications.append((method, params))

    async def request(self, method: str, params: dict):
        if method == "textDocument/diagnostic":
            if self._pull_raises:
                raise self._pull_raises
            return self._pull_response
        return None

    def get_cached_diagnostics(self, uri: str) -> list | None:
        return self._cached_diagnostics


def _make_lsp_diagnostic(
    message: str,
    severity: int = 1,
    start_line: int = 0,
    start_char: int = 0,
    end_line: int | None = None,
    end_char: int | None = None,
    code: str | int | None = None,
    source: str | None = None,
    related: list | None = None,
) -> dict:
    """Create an LSP diagnostic dict with 0-based line/character."""
    d: dict = {
        "message": message,
        "severity": severity,
        "range": {
            "start": {"line": start_line, "character": start_char},
            "end": {
                "line": end_line if end_line is not None else start_line,
                "character": end_char if end_char is not None else start_char + 5,
            },
        },
    }
    if code is not None:
        d["code"] = code
    if source is not None:
        d["source"] = source
    if related is not None:
        d["relatedInformation"] = related
    return d


# ── _format_diagnostics tests ───────────────────────────────────────────────


class TestFormatDiagnosticsEmpty:
    """_format_diagnostics with empty list should return zero counts."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_empty_returns_zero_counts(self, ops):
        result = ops._format_diagnostics([], "/path/to/main.rs")
        assert result["count"] == 0
        assert result["errors"] == 0
        assert result["warnings"] == 0
        assert result["diagnostics"] == []

    def test_empty_returns_source(self, ops):
        result = ops._format_diagnostics([], "/path/to/main.rs", source="pull")
        assert result["source"] == "pull"

    def test_empty_returns_message_with_filename(self, ops):
        result = ops._format_diagnostics([], "/path/to/main.rs")
        assert "main.rs" in result["message"]
        assert "No issues" in result["message"]


class TestFormatDiagnosticsErrors:
    """_format_diagnostics should format error diagnostics correctly."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_error_severity_mapped(self, ops):
        diags = [_make_lsp_diagnostic("type mismatch", severity=1)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["diagnostics"][0]["severity"] == "error"

    def test_error_counted(self, ops):
        diags = [_make_lsp_diagnostic("type mismatch", severity=1)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["errors"] == 1
        assert result["warnings"] == 0

    def test_line_1_based(self, ops):
        """LSP 0-based line should be converted to 1-based."""
        diags = [_make_lsp_diagnostic("err", start_line=4, start_char=10)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["diagnostics"][0]["line"] == 5
        assert result["diagnostics"][0]["character"] == 11

    def test_end_line_1_based(self, ops):
        """End positions should also be 1-based."""
        diags = [_make_lsp_diagnostic("err", end_line=6, end_char=20)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["diagnostics"][0]["end_line"] == 7
        assert result["diagnostics"][0]["end_character"] == 21

    def test_message_preserved(self, ops):
        diags = [_make_lsp_diagnostic("expected i32, found &str")]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["diagnostics"][0]["message"] == "expected i32, found &str"

    def test_code_included(self, ops):
        diags = [_make_lsp_diagnostic("type mismatch", code="E0308")]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["diagnostics"][0]["code"] == "E0308"

    def test_source_included(self, ops):
        diags = [_make_lsp_diagnostic("type mismatch", source="rustc")]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["diagnostics"][0]["source"] == "rustc"

    def test_code_absent_when_not_provided(self, ops):
        diags = [_make_lsp_diagnostic("some error")]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert "code" not in result["diagnostics"][0]

    def test_source_absent_when_not_provided(self, ops):
        diags = [_make_lsp_diagnostic("some error")]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert "source" not in result["diagnostics"][0]


class TestFormatDiagnosticsWarnings:
    """_format_diagnostics should handle warning severity."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_warning_severity_mapped(self, ops):
        diags = [_make_lsp_diagnostic("unused variable", severity=2)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["diagnostics"][0]["severity"] == "warning"

    def test_warning_counted(self, ops):
        diags = [_make_lsp_diagnostic("unused variable", severity=2)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["warnings"] == 1
        assert result["errors"] == 0


class TestFormatDiagnosticsMixed:
    """_format_diagnostics should handle mixed severities."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_mixed_severities_counted(self, ops):
        diags = [
            _make_lsp_diagnostic("error 1", severity=1),
            _make_lsp_diagnostic("warning 1", severity=2),
            _make_lsp_diagnostic("info 1", severity=3),
            _make_lsp_diagnostic("hint 1", severity=4),
            _make_lsp_diagnostic("error 2", severity=1),
        ]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["errors"] == 2
        assert result["warnings"] == 1
        assert result["count"] == 5

    def test_info_severity_mapped(self, ops):
        diags = [_make_lsp_diagnostic("info", severity=3)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["diagnostics"][0]["severity"] == "info"

    def test_hint_severity_mapped(self, ops):
        diags = [_make_lsp_diagnostic("hint", severity=4)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["diagnostics"][0]["severity"] == "hint"

    def test_message_summary(self, ops):
        diags = [
            _make_lsp_diagnostic("err", severity=1),
            _make_lsp_diagnostic("warn", severity=2),
        ]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert "1 error" in result["message"]
        assert "1 warning" in result["message"]
        assert "main.rs" in result["message"]


class TestFormatDiagnosticsRelatedInfo:
    """_format_diagnostics should include relatedInformation."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_related_info_included(self, ops):
        related = [
            {
                "message": "expected type defined here",
                "location": {
                    "uri": "file:///home/user/src/lib.rs",
                    "range": {
                        "start": {"line": 10, "character": 0},
                        "end": {"line": 10, "character": 5},
                    },
                },
            }
        ]
        diags = [_make_lsp_diagnostic("type mismatch", related=related)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        rel = result["diagnostics"][0]["related"]
        assert len(rel) == 1
        assert rel[0]["message"] == "expected type defined here"
        assert rel[0]["file"] == "/home/user/src/lib.rs"
        assert rel[0]["line"] == 11  # 1-based

    def test_related_info_capped_at_5(self, ops):
        related = [
            {
                "message": f"related {i}",
                "location": {
                    "uri": "file:///path/file.rs",
                    "range": {
                        "start": {"line": i, "character": 0},
                        "end": {"line": i, "character": 1},
                    },
                },
            }
            for i in range(10)
        ]
        diags = [_make_lsp_diagnostic("err", related=related)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert len(result["diagnostics"][0]["related"]) == 5

    def test_no_related_key_when_empty(self, ops):
        diags = [_make_lsp_diagnostic("err")]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert "related" not in result["diagnostics"][0]


class TestFormatDiagnosticsTruncation:
    """_format_diagnostics should cap at 100 diagnostics."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_truncated_at_100(self, ops):
        diags = [_make_lsp_diagnostic(f"error {i}") for i in range(150)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["count"] == 100
        assert result["truncated"] is True
        assert result["total_count"] == 150
        assert len(result["diagnostics"]) == 100

    def test_not_truncated_under_100(self, ops):
        diags = [_make_lsp_diagnostic(f"error {i}") for i in range(50)]
        result = ops._format_diagnostics(diags, "/path/main.rs")
        assert result["count"] == 50
        assert result["truncated"] is False
        assert result["total_count"] == 50


# ── _op_diagnostics tests ───────────────────────────────────────────────────


class TestOpDiagnosticsPull:
    """_op_diagnostics should try pull diagnostics first."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_pull_success_returns_formatted(self, ops, tmp_path):
        py_file = tmp_path / "main.rs"
        py_file.write_text("fn main() {}\n")
        pull_items = [_make_lsp_diagnostic("type mismatch", severity=1, code="E0308")]
        server = FakeDiagnosticsServer(
            pull_response={"items": pull_items},
        )
        result = await ops._op_diagnostics(server, str(py_file), 1, 1, None)
        assert result["source"] == "pull"
        assert result["count"] == 1
        assert result["diagnostics"][0]["severity"] == "error"

    @pytest.mark.asyncio
    async def test_pull_empty_items(self, ops, tmp_path):
        py_file = tmp_path / "main.rs"
        py_file.write_text("fn main() {}\n")
        server = FakeDiagnosticsServer(pull_response={"items": []})
        result = await ops._op_diagnostics(server, str(py_file), 1, 1, None)
        assert result["source"] == "pull"
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_pull_none_result(self, ops, tmp_path):
        """Pull returning None should be treated as empty."""
        py_file = tmp_path / "main.rs"
        py_file.write_text("fn main() {}\n")
        server = FakeDiagnosticsServer(pull_response=None)
        result = await ops._op_diagnostics(server, str(py_file), 1, 1, None)
        assert result["source"] == "pull"
        assert result["count"] == 0


class TestOpDiagnosticsPushFallback:
    """_op_diagnostics should fall back to push cache when pull fails."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_fallback_to_cache(self, ops, tmp_path):
        py_file = tmp_path / "main.rs"
        py_file.write_text("fn main() {}\n")
        cached = [_make_lsp_diagnostic("cached error", severity=1)]
        server = FakeDiagnosticsServer(
            pull_raises=Exception("Method not supported"),
            cached_diagnostics=cached,
        )
        result = await ops._op_diagnostics(server, str(py_file), 1, 1, None)
        assert result["source"] == "push_cache"
        assert result["count"] == 1
        assert result["diagnostics"][0]["message"] == "cached error"

    @pytest.mark.asyncio
    async def test_fallback_empty_cache_waits_then_returns_none_message(
        self, ops, tmp_path
    ):
        """When pull fails and cache is empty, wait then return helpful message."""
        py_file = tmp_path / "main.rs"
        py_file.write_text("fn main() {}\n")
        server = FakeDiagnosticsServer(
            pull_raises=Exception("Method not supported"),
            cached_diagnostics=None,
        )
        # Patch asyncio.sleep to not actually wait
        with patch(
            "amplifier_module_tool_lsp.operations.asyncio.sleep", new_callable=AsyncMock
        ):
            result = await ops._op_diagnostics(server, str(py_file), 1, 1, None)
        assert result["source"] == "none"
        assert result["count"] == 0
        assert (
            "try again" in result["message"].lower()
            or "still" in result["message"].lower()
        )

    @pytest.mark.asyncio
    async def test_fallback_cache_arrives_after_wait(self, ops, tmp_path):
        """If cache is None at first but available after sleep, use it."""
        py_file = tmp_path / "main.rs"
        py_file.write_text("fn main() {}\n")

        call_count = 0
        cached_diags = [_make_lsp_diagnostic("delayed error", severity=1)]

        class DelayedCacheServer(FakeDiagnosticsServer):
            def get_cached_diagnostics(self, uri: str) -> list | None:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return None  # First call: not yet available
                return cached_diags  # Second call: arrived

        server = DelayedCacheServer(
            pull_raises=Exception("Method not supported"),
        )
        with patch(
            "amplifier_module_tool_lsp.operations.asyncio.sleep", new_callable=AsyncMock
        ):
            result = await ops._op_diagnostics(server, str(py_file), 1, 1, None)
        assert result["source"] == "push_cache"
        assert result["diagnostics"][0]["message"] == "delayed error"


# ── tool.py integration: diagnostics no longer returns "not implemented" ────


class TestDiagnosticsNotInUnimplemented:
    """diagnostics should be removed from _NOT_YET_IMPLEMENTED_OPS."""

    def test_diagnostics_not_in_unimplemented(self):
        from amplifier_module_tool_lsp.tool import LspTool

        assert "diagnostics" not in LspTool._NOT_YET_IMPLEMENTED_OPS
