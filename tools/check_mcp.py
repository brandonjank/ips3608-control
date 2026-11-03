"""Read-only MCP smoke check. Optional connection preserves physical output."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent


def data(result):
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    return result.structured_content or json.loads(result.content[0].text)


async def check(port=None):
    transport = StdioServerParameters(command=sys.executable, args=[str(ROOT / "tools" / "run_mcp.py")])
    async with Client(transport) as client:
        tools = await client.list_tools()
        tools = tools.tools if hasattr(tools, "tools") else tools
        caps = data(await client.call_tool("get_capabilities"))
        status = data(await client.call_tool("get_status"))
        if port and not status["connected"]:
            data(await client.call_tool("connect", {"port": port}))
        for _ in range(20):
            status = data(await client.call_tool("get_status"))
            if status["fresh"] or not status["connected"]:
                break
            await asyncio.sleep(.25)
        observation = data(await client.call_tool("monitor", {"duration_s": 2}))
        status = observation["status"]
        caps = data(await client.call_tool("get_capabilities"))
        history = data(await client.call_tool("get_history", {"seconds": 60, "limit": 5}))
        resource = await client.read_resource("ips3608://status")
        device = status["device"]
        print(json.dumps({"tool_count": len(tools), "resource_read": bool(resource),
                          "connected": status["connected"], "fresh": status["fresh"],
                          "port": status["port"], "model": device.get("model"),
                          "hardware": device.get("hardware"), "firmware": device.get("firmware"),
                          "set_volts": device.get("set_volts"), "set_amps": device.get("set_amps"),
                          "supported_models": caps.get("supported_models"),
                          "model_profile": status.get("model_profile"), "limits": status.get("limits"),
                          "output_enabled": status["output_enabled"],
                          "monitor_samples": len(observation["samples"]),
                          "history_samples": history["count"], "latest_sample": status["latest_sample"]}, indent=2))
        if not status["fresh"]:
            raise RuntimeError("MCP is reachable but there are no fresh device readings.")
        if caps.get("active_model") != device.get("model") or not status.get("controls_available"):
            raise RuntimeError("Model identity/live limits are not ready for control.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", metavar="PORT", help="Connect app if disconnected; preserves output.")
    asyncio.run(check(parser.parse_args().connect))
