"""Response-stream failure must terminate without retaining a retry traceback."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from amplifier_module_tool_lsp.server import LspServer, ProxyLspServer


class BoundedReader(asyncio.StreamReader):
    """Fail a regression quickly even when the old loop never yields to timers."""
    calls = 0

    async def readline(self):
        self.calls += 1
        if self.calls > 20:
            raise asyncio.CancelledError("test cutoff; response loop did not stop")
        return await super().readline()


@pytest_asyncio.fixture(params=[LspServer, ProxyLspServer])
async def server(request, tmp_path):
    reader = BoundedReader()
    writer = SimpleNamespace(write=Mock(), drain=AsyncMock())
    if request.param is ProxyLspServer:
        client = request.param("python", tmp_path, reader, writer, 9999)
    else:
        client = request.param("python", tmp_path, SimpleNamespace(stdout=reader, stdin=writer))
    return client, reader, writer


@pytest.mark.asyncio
async def test_sticky_transport_error_ends_after_one_read(server):
    client, reader, _ = server
    original = ConnectionResetError("synthetic transport failure")
    reader.set_exception(original)
    pending = asyncio.get_running_loop().create_future()
    cancelled = asyncio.get_running_loop().create_future()
    cancelled.cancel()
    completed = asyncio.get_running_loop().create_future()
    completed.set_result("previous result")
    client._pending.update({1: pending, 2: cancelled, 3: completed})
    heartbeat = []
    asyncio.get_running_loop().call_soon(lambda: heartbeat.append(True))

    await client._read_responses()

    assert reader.calls == 1
    assert not client._pending
    with pytest.raises(ConnectionError, match="ConnectionResetError"):
        await pending
    assert pending.exception() is not original
    assert cancelled.cancelled() and completed.result() == "previous result"
    frames, traceback = 0, original.__traceback__
    while traceback is not None:
        frames += 1
        traceback = traceback.tb_next
    assert frames <= 4
    await asyncio.sleep(0)
    assert heartbeat == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize('termination', ['eof', 'error', 'cancel'])
async def test_terminal_reader_fails_pending_request_and_refuses_further_sends(server, termination):
    client, reader, writer = server
    pending = asyncio.create_task(client.request("textDocument/hover", {}))
    await asyncio.sleep(0)
    assert client._pending
    reading = asyncio.create_task(client._read_responses())
    await asyncio.sleep(0)
    if termination == 'eof':
        reader.feed_eof()
    elif termination == 'error':
        reader.set_exception(ConnectionResetError("synthetic transport failure"))
    else:
        reading.cancel()
    await reading  # Existing cancellation behavior: reader exits normally.
    with pytest.raises(ConnectionError, match="LSP response"):
        await pending
    assert not client._pending
    sent = writer.write.call_count
    with pytest.raises(ConnectionError, match="LSP response"):
        await client.request("textDocument/hover", {})
    with pytest.raises(ConnectionError, match="LSP response"):
        await client.notify("textDocument/didOpen", {})
    assert writer.write.call_count == sent
    assert not client._pending


@pytest.mark.asyncio
async def test_successful_response_and_notification_survive_normal_eof(server):
    client, reader, _ = server
    client._handle_notification = AsyncMock()
    pending = asyncio.create_task(client.request("textDocument/hover", {}))
    await asyncio.sleep(0)
    for message in [
        {"jsonrpc": "2.0", "id": 1, "result": {"contents": "found"}},
        {"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"diagnostics": []}},
    ]:
        body = json.dumps(message).encode()
        reader.feed_data(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    reader.feed_eof()
    await client._read_responses()
    assert await pending == {"contents": "found"}
    client._handle_notification.assert_awaited_once_with("textDocument/publishDiagnostics", {"diagnostics": []})
    assert not client._pending


@pytest.mark.asyncio
@pytest.mark.parametrize('termination', ['timeout', 'cancel', 'send_error'])
async def test_request_cleanup_without_a_reader_response(server, termination):
    client, _, writer = server
    client._default_timeout = .01
    if termination == 'send_error':
        writer.drain.side_effect = ConnectionError("synthetic send failure")
    pending = asyncio.create_task(client.request("textDocument/hover", {}))
    await asyncio.sleep(0)
    if termination == 'cancel':
        pending.cancel()
    expected = {'timeout': TimeoutError, 'cancel': asyncio.CancelledError, 'send_error': ConnectionError}[termination]
    with pytest.raises(expected):
        await pending
    assert not client._pending
