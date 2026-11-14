"""
test_mcp_server.py - พิสูจน์ว่า MCP Server ทำงานผ่านโปรโตคอลจริง

ไม่ใช่การ import ฟังก์ชัน Python มาเรียกตรง ๆ
สคริปต์นี้เปิด server เป็นอีก process แล้วคุยผ่าน MCP เท่านั้น

    python3 test_mcp_server.py
"""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    env = dict(os.environ, MCP_TRANSPORT="stdio", PYTHONPATH=os.getcwd())
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_server.server"], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = (await session.list_tools()).tools
            print("MCP Server ประกาศ tool %d ตัว" % len(tools))
            for t in tools:
                print("  - %-22s required: %s"
                      % (t.name, t.input_schema.get("required") or "ไม่มี"))

            print("\nเรียก resolve_filter_values('AIS')")
            r = await session.call_tool("resolve_filter_values", {"user_phrase": "AIS"})
            print(" ", r.content[0].text[:200].replace("\n", " "))

            print("\nเรียก query_complaints(group_by=['year'])")
            r = await session.call_tool("query_complaints",
                                        {"group_by": ["year"], "limit": 3})
            print(" ", r.content[0].text[:200].replace("\n", " "))

            print("\nเรียก query_complaints ด้วยคอลัมน์นอก whitelist")
            r = await session.call_tool("query_complaints",
                                        {"group_by": ["complaint_no; DROP TABLE complaints"]})
            print("  isError =", r.isError, "|", r.content[0].text[:120].replace("\n", " "))


if __name__ == "__main__":
    asyncio.run(main())
