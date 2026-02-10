"""Tests for operations.py Task 1 changes."""

import pytest

from amplifier_module_tool_lsp.operations import LspOperations
from amplifier_module_tool_lsp.server import LspServer


# ── Helpers ───────────────────────────────────────────────────────────────────


class FakeServer:
    """Fake LspServer that records notifications sent to it."""

    def __init__(self):
        self.language = "python"
        self.notifications: list[tuple[str, dict]] = []

    async def notify(self, method: str, params: dict):
        self.notifications.append((method, params))

    async def request(self, method: str, params: dict):
        return None


# ── Step 1.7: Track open documents ───────────────────────────────────────────


class TestDocumentTracking:
    """_open_document should track documents and avoid redundant didOpen."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_open_documents_tracking_exists(self, ops):
        """LspOperations should have _open_documents dict."""
        assert hasattr(ops, "_open_documents")
        assert isinstance(ops._open_documents, dict)

    def test_doc_versions_tracking_exists(self, ops):
        """LspOperations should have _doc_versions dict."""
        assert hasattr(ops, "_doc_versions")
        assert isinstance(ops._doc_versions, dict)

    @pytest.mark.asyncio
    async def test_first_open_sends_did_open(self, ops, tmp_path):
        """First call to _open_document should send textDocument/didOpen."""
        server = FakeServer()
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._open_document(server, str(py_file))

        assert len(server.notifications) == 1
        method, params = server.notifications[0]
        assert method == "textDocument/didOpen"
        assert params["textDocument"]["version"] == 1
        assert params["textDocument"]["text"] == "x = 1\n"

    @pytest.mark.asyncio
    async def test_second_open_unchanged_skips(self, ops, tmp_path):
        """Second call with unchanged file should send nothing."""
        server = FakeServer()
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._open_document(server, str(py_file))
        server.notifications.clear()

        await ops._open_document(server, str(py_file))
        assert len(server.notifications) == 0, "Should skip unchanged file"

    @pytest.mark.asyncio
    async def test_second_open_changed_sends_did_change(self, ops, tmp_path):
        """Second call after file changed should send textDocument/didChange."""
        server = FakeServer()
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        await ops._open_document(server, str(py_file))
        server.notifications.clear()

        # Modify the file
        py_file.write_text("x = 2\n")

        await ops._open_document(server, str(py_file))

        assert len(server.notifications) == 1
        method, params = server.notifications[0]
        assert method == "textDocument/didChange"
        assert params["textDocument"]["version"] == 2

    @pytest.mark.asyncio
    async def test_version_increments(self, ops, tmp_path):
        """Version should increment on each change."""
        server = FakeServer()
        py_file = tmp_path / "test.py"

        py_file.write_text("v1\n")
        await ops._open_document(server, str(py_file))

        py_file.write_text("v2\n")
        await ops._open_document(server, str(py_file))

        py_file.write_text("v3\n")
        await ops._open_document(server, str(py_file))

        # Should have: didOpen(v=1), didChange(v=2), didChange(v=3)
        assert len(server.notifications) == 3
        assert server.notifications[0][1]["textDocument"]["version"] == 1
        assert server.notifications[1][1]["textDocument"]["version"] == 2
        assert server.notifications[2][1]["textDocument"]["version"] == 3

    @pytest.mark.asyncio
    async def test_did_change_contains_full_content(self, ops, tmp_path):
        """didChange should use full content replacement."""
        server = FakeServer()
        py_file = tmp_path / "test.py"

        py_file.write_text("original\n")
        await ops._open_document(server, str(py_file))
        server.notifications.clear()

        py_file.write_text("modified\n")
        await ops._open_document(server, str(py_file))

        method, params = server.notifications[0]
        assert method == "textDocument/didChange"
        # Full replacement means contentChanges has one entry with just text
        changes = params["contentChanges"]
        assert len(changes) == 1
        assert changes[0]["text"] == "modified\n"

    @pytest.mark.asyncio
    async def test_different_files_tracked_independently(self, ops, tmp_path):
        """Each file should be tracked independently."""
        server = FakeServer()
        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        file_a.write_text("a = 1\n")
        file_b.write_text("b = 1\n")

        await ops._open_document(server, str(file_a))
        await ops._open_document(server, str(file_b))

        # Both should have sent didOpen
        assert len(server.notifications) == 2
        assert server.notifications[0][0] == "textDocument/didOpen"
        assert server.notifications[1][0] == "textDocument/didOpen"


# ── Step (from plan): _uri_to_path utility ───────────────────────────────────


class TestUriToPath:
    """_uri_to_path should convert file:// URIs to filesystem paths."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_simple_path(self, ops):
        result = ops._uri_to_path("file:///home/user/test.py")
        assert result == "/home/user/test.py"

    def test_encoded_spaces(self, ops):
        result = ops._uri_to_path("file:///home/user/my%20project/test.py")
        assert result == "/home/user/my project/test.py"

    def test_encoded_special_chars(self, ops):
        result = ops._uri_to_path("file:///home/user/%23special/test.py")
        assert result == "/home/user/#special/test.py"
