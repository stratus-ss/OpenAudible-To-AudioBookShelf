#!/usr/bin/env python3
"""Bridge between bash test script and the MCP server.

Reads line-based commands from stdin, dispatches them to the FastMCP
instance, and writes single-line JSON results to stdout.

Protocol
--------
Commands (stdin, one per line):
    TOOLS                               -> list tool names
    CALL <tool_name> [<json_args>]      -> invoke a tool
    QUIT                                -> shut down

Responses (stdout, one JSON object per line):
    {"tools": [...]}
    {"text": "..."}
    {"error": "..."}
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server import mcp


async def _handle(line: str) -> dict:
    parts = line.split(None, 2)
    cmd = parts[0].upper()

    if cmd == "TOOLS":
        tools = await mcp.list_tools()
        return {"tools": sorted(t.name for t in tools)}

    if cmd == "CALL":
        name = parts[1]
        args = json.loads(parts[2]) if len(parts) > 2 else {}
        result = await mcp.call_tool(name, args)
        return {"text": result[0][0].text}

    return {"error": f"unknown command: {cmd}"}


async def main() -> None:
    print("READY", flush=True)
    loop = asyncio.get_event_loop()

    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        line = line.strip()
        if not line or line == "QUIT":
            break
        try:
            result = await _handle(line)
        except Exception as exc:
            result = {"error": str(exc)}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
