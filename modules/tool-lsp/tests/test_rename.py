"""Tests for Task 4: Rename operation."""

import pytest

from amplifier_module_tool_lsp.operations import LspOperations
from amplifier_module_tool_lsp.tool import LspTool


# ── Helpers ──────────────────────────────────────────────────────────────────


class RenameServer:
    """Fake server that returns configurable results for rename-related methods."""

    def __init__(self, responses: dict | None = None):
        self.language = "python"
        self.notifications: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, dict]] = []
        self._responses = responses or {}

    async def notify(self, method: str, params: dict):
        self.notifications.append((method, params))

    async def request(self, method: str, params: dict):
        self.requests.append((method, params))
        if method in self._responses:
            value = self._responses[method]
            if isinstance(value, Exception):
                raise value
            return value
        return None


# ── _format_workspace_edit: "changes" format ─────────────────────────────────


class TestFormatWorkspaceEditChanges:
    """_format_workspace_edit should handle the 'changes' format correctly."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_single_file_single_edit(self, ops):
        """Single edit in changes format with correct 1-based positions."""
        workspace_edit = {
            "changes": {
                "file:///home/user/src/main.py": [
                    {
                        "range": {
                            "start": {"line": 4, "character": 3},
                            "end": {"line": 4, "character": 19},
                        },
                        "newText": "compute_sum",
                    }
                ]
            }
        }
        result = ops._format_workspace_edit(workspace_edit, "Rename to 'compute_sum'")

        assert result["description"] == "Rename to 'compute_sum'"
        assert result["files_affected"] == 1
        assert result["total_edits"] == 1
        assert result["truncated"] is False
        assert len(result["edits"]) == 1

        edit = result["edits"][0]
        assert edit["file"] == "/home/user/src/main.py"
        assert edit["start_line"] == 5  # 0-based 4 → 1-based 5
        assert edit["start_character"] == 4  # 0-based 3 → 1-based 4
        assert edit["end_line"] == 5
        assert edit["end_character"] == 20  # 0-based 19 → 1-based 20
        assert edit["new_text"] == "compute_sum"

    def test_multiple_files_multiple_edits(self, ops):
        """Multiple edits across multiple files."""
        workspace_edit = {
            "changes": {
                "file:///home/user/src/main.py": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 3},
                            "end": {"line": 0, "character": 18},
                        },
                        "newText": "compute_sum",
                    }
                ],
                "file:///home/user/src/lib.py": [
                    {
                        "range": {
                            "start": {"line": 9, "character": 4},
                            "end": {"line": 9, "character": 19},
                        },
                        "newText": "compute_sum",
                    },
                    {
                        "range": {
                            "start": {"line": 14, "character": 8},
                            "end": {"line": 14, "character": 23},
                        },
                        "newText": "compute_sum",
                    },
                ],
            }
        }
        result = ops._format_workspace_edit(workspace_edit, "Rename")

        assert result["files_affected"] == 2
        assert result["total_edits"] == 3
        assert len(result["edits"]) == 3

    def test_uri_to_path_conversion(self, ops):
        """File URIs should be converted to filesystem paths."""
        workspace_edit = {
            "changes": {
                "file:///home/user/my%20project/test.py": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 5},
                        },
                        "newText": "new_name",
                    }
                ]
            }
        }
        result = ops._format_workspace_edit(workspace_edit, "Rename")
        assert result["edits"][0]["file"] == "/home/user/my project/test.py"

    def test_message_format(self, ops):
        """Message should describe edit count and file count."""
        workspace_edit = {
            "changes": {
                "file:///a.py": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "newText": "x",
                    }
                ],
                "file:///b.py": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "newText": "x",
                    }
                ],
            }
        }
        result = ops._format_workspace_edit(workspace_edit, "Rename")
        assert "2 edit(s)" in result["message"]
        assert "2 file(s)" in result["message"]


# ── _format_workspace_edit: "documentChanges" format ─────────────────────────


class TestFormatWorkspaceEditDocumentChanges:
    """_format_workspace_edit should handle 'documentChanges' format."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_text_document_edit(self, ops):
        """TextDocumentEdit entries should be parsed with 1-based positions."""
        workspace_edit = {
            "documentChanges": [
                {
                    "textDocument": {
                        "uri": "file:///home/user/src/main.py",
                        "version": 1,
                    },
                    "edits": [
                        {
                            "range": {
                                "start": {"line": 2, "character": 5},
                                "end": {"line": 2, "character": 20},
                            },
                            "newText": "new_func",
                        }
                    ],
                }
            ]
        }
        result = ops._format_workspace_edit(workspace_edit, "Rename")

        assert result["files_affected"] == 1
        assert result["total_edits"] == 1
        edit = result["edits"][0]
        assert edit["file"] == "/home/user/src/main.py"
        assert edit["start_line"] == 3  # 0-based 2 → 1-based 3
        assert edit["start_character"] == 6  # 0-based 5 → 1-based 6
        assert edit["new_text"] == "new_func"

    def test_resource_operation_rename(self, ops):
        """RenameFile resource operation should produce file_operation entry."""
        workspace_edit = {
            "documentChanges": [
                {
                    "kind": "rename",
                    "oldUri": "file:///home/user/old_name.py",
                    "newUri": "file:///home/user/new_name.py",
                }
            ]
        }
        result = ops._format_workspace_edit(workspace_edit, "Rename file")

        assert result["total_edits"] == 1
        edit = result["edits"][0]
        assert edit["file_operation"] == "rename"
        assert edit["old_path"] == "/home/user/old_name.py"
        assert edit["new_path"] == "/home/user/new_name.py"

    def test_resource_operation_create(self, ops):
        """CreateFile resource operation should produce file_operation entry."""
        workspace_edit = {
            "documentChanges": [
                {
                    "kind": "create",
                    "uri": "file:///home/user/new_file.py",
                }
            ]
        }
        result = ops._format_workspace_edit(workspace_edit, "Create")

        assert result["total_edits"] == 1
        assert result["edits"][0]["file_operation"] == "create"
        assert result["edits"][0]["path"] == "/home/user/new_file.py"

    def test_resource_operation_delete(self, ops):
        """DeleteFile resource operation should produce file_operation entry."""
        workspace_edit = {
            "documentChanges": [
                {
                    "kind": "delete",
                    "uri": "file:///home/user/old_file.py",
                }
            ]
        }
        result = ops._format_workspace_edit(workspace_edit, "Delete")

        assert result["total_edits"] == 1
        assert result["edits"][0]["file_operation"] == "delete"
        assert result["edits"][0]["path"] == "/home/user/old_file.py"

    def test_mixed_text_edits_and_resource_ops(self, ops):
        """documentChanges with both text edits and resource operations."""
        workspace_edit = {
            "documentChanges": [
                {
                    "textDocument": {
                        "uri": "file:///home/user/main.py",
                        "version": 1,
                    },
                    "edits": [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 5},
                            },
                            "newText": "new_name",
                        }
                    ],
                },
                {
                    "kind": "rename",
                    "oldUri": "file:///home/user/old.py",
                    "newUri": "file:///home/user/new.py",
                },
            ]
        }
        result = ops._format_workspace_edit(workspace_edit, "Rename")

        assert result["total_edits"] == 2
        assert result["files_affected"] == 2  # main.py + old.py

    def test_changes_preferred_over_document_changes(self, ops):
        """When both 'changes' and 'documentChanges' are present, use 'changes'."""
        workspace_edit = {
            "changes": {
                "file:///a.py": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "newText": "x",
                    }
                ]
            },
            "documentChanges": [
                {
                    "textDocument": {"uri": "file:///b.py", "version": 1},
                    "edits": [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 1},
                            },
                            "newText": "y",
                        },
                        {
                            "range": {
                                "start": {"line": 1, "character": 0},
                                "end": {"line": 1, "character": 1},
                            },
                            "newText": "z",
                        },
                    ],
                }
            ],
        }
        result = ops._format_workspace_edit(workspace_edit, "Rename")

        # Should use 'changes' (1 edit), not 'documentChanges' (2 edits)
        assert result["total_edits"] == 1
        assert result["edits"][0]["new_text"] == "x"


# ── _format_workspace_edit: truncation ───────────────────────────────────────


class TestFormatWorkspaceEditTruncation:
    """Edits should be capped at 200."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    def test_truncation_at_200_edits(self, ops):
        """More than 200 edits should be truncated."""
        many_edits = []
        for i in range(250):
            many_edits.append(
                {
                    "range": {
                        "start": {"line": i, "character": 0},
                        "end": {"line": i, "character": 5},
                    },
                    "newText": "x",
                }
            )
        workspace_edit = {"changes": {"file:///big.py": many_edits}}
        result = ops._format_workspace_edit(workspace_edit, "Rename")

        assert result["total_edits"] == 250
        assert len(result["edits"]) == 200
        assert result["truncated"] is True

    def test_exactly_200_not_truncated(self, ops):
        """Exactly 200 edits should NOT be marked as truncated."""
        edits = []
        for i in range(200):
            edits.append(
                {
                    "range": {
                        "start": {"line": i, "character": 0},
                        "end": {"line": i, "character": 5},
                    },
                    "newText": "x",
                }
            )
        workspace_edit = {"changes": {"file:///a.py": edits}}
        result = ops._format_workspace_edit(workspace_edit, "Rename")

        assert result["total_edits"] == 200
        assert len(result["edits"]) == 200
        assert result["truncated"] is False


# ── _op_rename ───────────────────────────────────────────────────────────────


class TestOpRename:
    """_op_rename should perform rename via LSP and return formatted results."""

    @pytest.fixture
    def ops(self):
        return LspOperations(server_manager=None)

    @pytest.mark.asyncio
    async def test_successful_rename_with_changes_format(self, ops, tmp_path):
        """Successful rename should return formatted workspace edit."""
        py_file = tmp_path / "main.py"
        py_file.write_text("def calculate_total():\n    pass\n")

        rename_result = {
            "changes": {
                py_file.resolve().as_uri(): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 19},
                        },
                        "newText": "compute_sum",
                    }
                ]
            }
        }
        server = RenameServer(
            responses={
                "textDocument/prepareRename": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 19},
                },
                "textDocument/rename": rename_result,
            }
        )

        result = await ops._op_rename(server, str(py_file), 1, 5, newName="compute_sum")

        assert result["operation"] == "rename"
        assert result["new_name"] == "compute_sum"
        assert result["files_affected"] == 1
        assert result["total_edits"] == 1
        assert result["edits"][0]["new_text"] == "compute_sum"
        assert result["edits"][0]["start_line"] == 1  # 0-based 0 → 1-based 1
        assert result["edits"][0]["start_character"] == 5  # 0-based 4 → 1-based 5

    @pytest.mark.asyncio
    async def test_successful_rename_with_document_changes_format(self, ops, tmp_path):
        """Rename with documentChanges format should also work."""
        py_file = tmp_path / "main.py"
        py_file.write_text("def foo():\n    pass\n")

        rename_result = {
            "documentChanges": [
                {
                    "textDocument": {
                        "uri": py_file.resolve().as_uri(),
                        "version": 1,
                    },
                    "edits": [
                        {
                            "range": {
                                "start": {"line": 0, "character": 4},
                                "end": {"line": 0, "character": 7},
                            },
                            "newText": "bar",
                        }
                    ],
                }
            ]
        }
        server = RenameServer(
            responses={
                "textDocument/prepareRename": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 7},
                },
                "textDocument/rename": rename_result,
            }
        )

        result = await ops._op_rename(server, str(py_file), 1, 5, newName="bar")

        assert result["operation"] == "rename"
        assert result["new_name"] == "bar"
        assert result["total_edits"] == 1
        assert result["edits"][0]["new_text"] == "bar"

    @pytest.mark.asyncio
    async def test_missing_new_name_returns_error(self, ops, tmp_path):
        """Missing newName should return error dict."""
        py_file = tmp_path / "main.py"
        py_file.write_text("x = 1\n")

        server = RenameServer()
        result = await ops._op_rename(server, str(py_file), 1, 1)

        assert "error" in result
        assert "newName" in result["error"]

    @pytest.mark.asyncio
    async def test_prepare_rename_returns_none(self, ops, tmp_path):
        """prepareRename returning None means symbol cannot be renamed."""
        py_file = tmp_path / "main.py"
        py_file.write_text("fn = 1\n")

        server = RenameServer(
            responses={
                "textDocument/prepareRename": None,
            }
        )

        result = await ops._op_rename(server, str(py_file), 1, 1, newName="new_name")

        assert result.get("success") is False
        assert "cannot be renamed" in result["message"]

    @pytest.mark.asyncio
    async def test_prepare_rename_raises_exception_fallback(self, ops, tmp_path):
        """If prepareRename raises, fall back to rename directly."""
        py_file = tmp_path / "main.py"
        py_file.write_text("def foo():\n    pass\n")

        rename_result = {
            "changes": {
                py_file.resolve().as_uri(): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 7},
                        },
                        "newText": "bar",
                    }
                ]
            }
        }
        server = RenameServer(
            responses={
                "textDocument/prepareRename": RuntimeError("not supported"),
                "textDocument/rename": rename_result,
            }
        )

        result = await ops._op_rename(server, str(py_file), 1, 5, newName="bar")

        # Should succeed despite prepareRename failing
        assert result["operation"] == "rename"
        assert result["new_name"] == "bar"
        assert result["total_edits"] == 1

    @pytest.mark.asyncio
    async def test_rename_no_result(self, ops, tmp_path):
        """Rename returning None/empty should report no edits."""
        py_file = tmp_path / "main.py"
        py_file.write_text("x = 1\n")

        server = RenameServer(
            responses={
                "textDocument/prepareRename": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 1},
                },
                "textDocument/rename": None,
            }
        )

        result = await ops._op_rename(server, str(py_file), 1, 1, newName="y")

        assert result.get("success") is False
        assert (
            "no edits" in result["message"].lower()
            or "not renameable" in result["message"].lower()
        )

    @pytest.mark.asyncio
    async def test_rename_sends_correct_lsp_params(self, ops, tmp_path):
        """Verify rename sends correct 0-based params to server."""
        py_file = tmp_path / "main.py"
        py_file.write_text("def foo():\n    pass\n")

        rename_result = {
            "changes": {
                py_file.resolve().as_uri(): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 7},
                        },
                        "newText": "bar",
                    }
                ]
            }
        }
        server = RenameServer(
            responses={
                "textDocument/prepareRename": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 7},
                },
                "textDocument/rename": rename_result,
            }
        )

        await ops._op_rename(server, str(py_file), 1, 5, newName="bar")

        # Check the rename request params (should be 0-based)
        rename_requests = [
            (m, p) for m, p in server.requests if m == "textDocument/rename"
        ]
        assert len(rename_requests) == 1
        _, params = rename_requests[0]
        assert params["position"]["line"] == 0  # 1-based 1 → 0-based 0
        assert params["position"]["character"] == 4  # 1-based 5 → 0-based 4
        assert params["newName"] == "bar"


# ── tool.py: rename validation ───────────────────────────────────────────────


class TestRenameToolValidation:
    """tool.py should validate newName for rename and no longer return 'not implemented'."""

    @pytest.fixture
    def tool(self):
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

    def test_rename_not_in_not_yet_implemented(self, tool):
        """rename should NOT be in _NOT_YET_IMPLEMENTED_OPS."""
        assert "rename" not in tool._NOT_YET_IMPLEMENTED_OPS

    @pytest.mark.asyncio
    async def test_rename_missing_new_name_returns_error(self, tool, tmp_path):
        """Calling rename without newName should return error at tool level."""
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\n")

        result = await tool.execute(
            {
                "operation": "rename",
                "file_path": str(py_file),
                "line": 1,
                "character": 1,
            }
        )

        assert result.success is False
        assert "newName" in str(result.error) or "newname" in str(result.error).lower()
