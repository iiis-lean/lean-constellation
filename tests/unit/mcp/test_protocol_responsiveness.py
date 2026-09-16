import asyncio
from threading import Event, get_ident

from lean_constellation.mcp import stdio
from tests.unit.mcp._helpers import make_mcp_runtime


class CapturingServer:
    def __init__(self, name):
        self.name = name

    def list_tools(self):
        def register(callback):
            self.list_handler = callback
            return callback
        return register

    def call_tool(self, **kwargs):
        def register(callback):
            self.call_handler = callback
            return callback
        return register


def test_slow_tool_keeps_catalog_responsive_and_serializes_other_views(monkeypatch):
    monkeypatch.setattr(stdio, 'Server', CapturingServer)
    monkeypatch.setattr(stdio, '_current_request_headers', lambda _: {'request': 'identity'})
    runtime = make_mcp_runtime()
    first = stdio.create_mcp_protocol_server(runtime, view_key='repo_format_discovery_submit').value
    second = stdio.create_mcp_protocol_server(runtime, view_key='repo_mathlib_recon').value
    entered, release = Event(), Event()
    calls = []
    main_thread = get_ident()

    def slow(endpoint, name, arguments, *, headers, env):
        calls.append((name, get_ident(), headers))
        if name == 'slow':
            entered.set()
            assert release.wait(3)
        return name

    monkeypatch.setattr(stdio, 'mcp_protocol_call_tool', slow)

    async def run():
        task = asyncio.create_task(first.call_handler('slow', {}))
        other = None
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.01)
            assert entered.is_set()
            assert not task.done()
            # Tool directory remains available while a Lean-like call blocks.
            assert await second.list_handler()
            other = asyncio.create_task(second.call_handler('second', {}))
            await asyncio.sleep(.02)
            assert len(calls) == 1
        finally:
            release.set()
        assert await task == 'slow'
        assert await other == 'second'

    asyncio.run(run())
    assert [c[0] for c in calls] == ['slow', 'second']
    assert all(c[1] != main_thread for c in calls)
    assert all(c[2] == {'request': 'identity'} for c in calls)


def test_request_cancellation_does_not_abandon_tool_mutation(monkeypatch):
    import anyio
    monkeypatch.setattr(stdio, 'Server', CapturingServer)
    monkeypatch.setattr(stdio, '_current_request_headers', lambda _: {})
    protocol = stdio.create_mcp_protocol_server(make_mcp_runtime(), view_key='repo_format_discovery_submit').value
    entered, release, committed = Event(), Event(), Event()
    scopes = []
    returned = []

    def mutation(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        committed.set()
        return 'committed'

    monkeypatch.setattr(stdio, 'mcp_protocol_call_tool', mutation)

    async def run():
        async def request():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                returned.append(await protocol.call_handler('mutation', {}))
        async with anyio.create_task_group() as group:
            group.start_soon(request)
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await anyio.sleep(.01)
                assert entered.is_set()
                scopes[0].cancel()
                await anyio.sleep(.02)
                assert not committed.is_set()
                assert not returned
            finally:
                release.set()
        assert committed.is_set()
        assert returned == ['committed']

    anyio.run(run)


def test_http_initialize_and_catalog_respond_during_blocking_tool(monkeypatch):
    import anyio
    import httpx
    from mcp import ClientSession, types
    from mcp.client.streamable_http import streamable_http_client
    from lean_constellation.mcp.http import create_mcp_http_app

    entered, release = Event(), Event()
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return types.CallToolResult(content=[types.TextContent(type='text', text='done')])
    monkeypatch.setattr(stdio, 'mcp_protocol_call_tool', slow)
    app = create_mcp_http_app(make_mcp_runtime(), view_keys=['resource_curator']).value

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                url = 'http://testserver/mcp/views/resource_curator/'
                async with streamable_http_client(url, http_client=client, terminate_on_close=False) as (r, w, _):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        pending = asyncio.create_task(session.call_tool('normalize_resource_target', {'target': 'https://example.com'}))
                        try:
                            for _ in range(100):
                                if entered.is_set():
                                    break
                                await anyio.sleep(.01)
                            assert entered.is_set() and not pending.done()
                            async with streamable_http_client(url, http_client=client, terminate_on_close=False) as (r2, w2, _):
                                async with ClientSession(r2, w2) as fresh:
                                    with anyio.fail_after(1):
                                        await fresh.initialize()
                                        assert (await fresh.list_tools()).tools
                            assert not pending.done()
                        finally:
                            release.set()
                        assert not (await pending).isError
    anyio.run(run)
