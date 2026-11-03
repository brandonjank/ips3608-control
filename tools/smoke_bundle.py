"""Initialize the packaged MCP executable and validate offline tools. No hardware."""

import asyncio
import json
import os
from pathlib import Path
import tempfile

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent


def data(result):
    assert not result.is_error, result.content
    return result.structured_content or json.loads(result.content[0].text)


async def main():
    server = ROOT / "dist" / "windows" / "IPS3608-Control" / "IPS3608ControlMCP.exe"
    with tempfile.TemporaryDirectory() as directory:
        transport = StdioServerParameters(command=str(server), args=[],
            env={**os.environ, "IPS3608_CONTROL_HOME": directory}, cwd=directory)
        async with Client(transport, read_timeout_seconds=30) as client:
            tools = await client.list_tools()
            tools = tools.tools if hasattr(tools, "tools") else tools
            assert len(tools) == 21
            caps = data(await client.call_tool("get_capabilities"))
            assert caps["supported_models"] == ["IPS3608", "DPS-150", "DPS-150P"]
            result = data(await client.call_tool("preview_sweep", dict(model="DPS-150P", axis="voltage",
                start=5, end=6, increment=.5, fixed=.2, seconds=1)))
            assert len(result["steps"]) == 3 and result["validation_scope"] == "rated"
            resource = await client.read_resource("ips3608://capabilities")
            assert "DPS-150P" in str(resource)
            status = await client.call_tool("get_status")
            assert status.is_error, "The isolated smoke test must not discover a live app."
    print("Packaged MCP: initialization, 21 tools, resources, model preview and app-closed error verified.")


if __name__ == "__main__":
    asyncio.run(main())
