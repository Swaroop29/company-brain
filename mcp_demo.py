"""Read a saved incident through the MCP server.

Start the server first:
  mcp run mcp_server.py:mcp --transport streamable-http

Then run:
  python mcp_demo.py INC-1
"""
import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(incident_id):
    async with streamable_http_client("http://127.0.0.1:8000/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("MCP tools:", ", ".join(tool.name for tool in tools.tools))
            result = await session.call_tool("read_incident_memory", {"incident_id": incident_id})
            if result.is_error:
                raise RuntimeError("MCP server reported an error")
            payload = "".join(block.text for block in result.content if block.type == "text")
            print(json.dumps(json.loads(payload), indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python mcp_demo.py INC-1")
    asyncio.run(main(sys.argv[1]))
