"""Verify deployed MCP and OAuth discovery with the official SDK; read tools only."""
import asyncio


def verify(url, token):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def check():
        async with httpx.AsyncClient(headers={'Authorization': 'Bearer ' + token},
                                     timeout=45, follow_redirects=False) as client:
            discovery = await client.get(url.rstrip('/') + '/.well-known/oauth-authorization-server')
            discovery.raise_for_status()
            metadata = discovery.json()
            if ((metadata.get('issuer') or '').rstrip('/') != url.rstrip('/')
                    or metadata.get('registration_endpoint') != url.rstrip('/') + '/register'):
                raise ValueError('Deployed OAuth issuer does not match the service URL')
            async with streamable_http_client(url.rstrip('/') + '/mcp', http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    if 'journalQueueCounts' not in {tool.name for tool in listing.tools}:
                        raise ValueError('Deployed MCP tool catalog is incomplete')
                    result = await session.call_tool('journalQueueCounts', {})
                    if result.isError:
                        raise ValueError('MCP queue read failed')
        return len(listing.tools)

    return asyncio.run(check())
