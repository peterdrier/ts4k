"""Spot-check: dump raw MCP response for a single message to see what we're getting."""

import argparse
import asyncio
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def run(email: str, msg_id: str, server_url: str):
    async with streamablehttp_client(server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_gmail_message_content",
                {"user_google_email": email, "message_id": msg_id},
            )
            text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
            print(f"Raw response: {len(text)} bytes")
            print("=" * 70)
            print(text[:5000])
            if len(text) > 5000:
                print(f"\n... ({len(text) - 5000} more bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spot-check a single message from MCP")
    parser.add_argument("email", help="Google email")
    parser.add_argument("msg_id", help="Gmail message ID")
    parser.add_argument("--url", default="http://localhost:51429/mcp")
    args = parser.parse_args()
    asyncio.run(run(args.email, args.msg_id, args.url))
