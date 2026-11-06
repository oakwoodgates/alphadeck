# tests/agent_tools/dump_contract.py (committed; also the review tool): prints sha256 + the JSON to stdout
import asyncio
import hashlib
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(command, args):
    async with stdio_client(StdioServerParameters(command=command, args=args)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [
                t.model_dump(mode="json", exclude_none=True) for t in (await s.list_tools()).tools
            ]
            prompts = [
                p.model_dump(mode="json", exclude_none=True)
                for p in (await s.list_prompts()).prompts
            ]
            text = json.dumps(
                {"tools": tools, "prompts": prompts}, sort_keys=True, separators=(",", ":")
            )
            print(hashlib.sha256(text.encode()).hexdigest())
            print(text)


asyncio.run(main(sys.argv[1], sys.argv[2:]))
