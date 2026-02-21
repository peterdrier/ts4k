"""Real-world probe: fetch 100 messages from live Gmail MCP, normalize, report stats."""

import argparse
import asyncio
import json
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from ts4k.core.normalize import normalize, normalize_headers
from ts4k.core.format import format_listing, format_message, estimate_size
from ts4k.adapters.gmail import parse_search_response, parse_message_response


async def run(email: str, server_url: str, count: int):
    async with streamablehttp_client(server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected to google_workspace_mcp")

            # Step 1: search for 100 recent messages
            search_result = await session.call_tool(
                "search_gmail_messages",
                {
                    "user_google_email": email,
                    "query": "newer_than:30d",
                    "page_size": count,
                },
            )
            search_text = "\n".join(c.text for c in search_result.content if hasattr(c, "text"))
            search_bytes = len(search_text.encode())
            print(f"Raw search response ({search_bytes} bytes):\n---\n{search_text[:2000]}\n---")
            entries = parse_search_response(search_text, "g")
            print(f"Parsed {len(entries)} messages")

            # Step 2: fetch each message and normalize
            raw_total = 0
            norm_total = 0
            pipe_listing_data = []
            per_msg_stats = []

            for i, entry in enumerate(entries):
                raw_id = entry["raw_id"]
                try:
                    msg_result = await session.call_tool(
                        "get_gmail_message_content",
                        {
                            "user_google_email": email,
                            "message_id": raw_id,
                        },
                    )
                    msg_text = "\n".join(c.text for c in msg_result.content if hasattr(c, "text"))
                    msg_bytes = len(msg_text.encode())
                    raw_total += msg_bytes

                    # Parse into structured dict
                    parsed = parse_message_response(msg_text, "g", raw_id)

                    # Normalize
                    norm_body = normalize(parsed["body"])
                    norm_hdrs = normalize_headers({
                        "from": parsed.get("from", ""),
                        "subject": parsed.get("subject", ""),
                        "date": parsed.get("date", ""),
                    })

                    norm_msg = {**norm_hdrs, "id": parsed["id"], "body": norm_body, "source": "g"}
                    formatted = format_message(norm_msg, "pipe")
                    fmt_bytes = len(formatted.encode())
                    norm_total += fmt_bytes

                    saving = 1.0 - (fmt_bytes / msg_bytes) if msg_bytes > 0 else 0
                    per_msg_stats.append({
                        "id": raw_id[:12],
                        "from": parsed.get("from", "?")[:30],
                        "subject": parsed.get("subject", "?")[:40],
                        "raw": msg_bytes,
                        "norm": fmt_bytes,
                        "saving": saving,
                    })

                    pipe_listing_data.append(norm_msg)

                    status = f"[{i+1}/{len(entries)}] {saving:.0%} saved  {msg_bytes:>6,}b -> {fmt_bytes:>5,}b  {parsed.get('from','')[:25]}"
                    print(status)

                except Exception as e:
                    print(f"[{i+1}/{len(entries)}] ERROR fetching {raw_id[:12]}: {e}")

            # Step 3: summary
            print("\n" + "=" * 70)
            print("REAL-WORLD PROBE RESULTS")
            print("=" * 70)
            print(f"Messages fetched:    {len(per_msg_stats)}")
            print(f"Raw MCP total:       {raw_total:>10,} bytes")
            print(f"Normalized total:    {norm_total:>10,} bytes")
            overall = 1.0 - (norm_total / raw_total) if raw_total > 0 else 0
            print(f"Overall saving:      {overall:.1%}")

            savings = [s["saving"] for s in per_msg_stats]
            if savings:
                print(f"Per-message min:     {min(savings):.1%}")
                print(f"Per-message max:     {max(savings):.1%}")
                print(f"Per-message median:  {sorted(savings)[len(savings)//2]:.1%}")

            # Listing format comparison
            listing_out = format_listing(pipe_listing_data, "pipe")
            listing_bytes = len(listing_out.encode())
            print(f"\nListing (pipe):      {listing_bytes:>10,} bytes for {len(pipe_listing_data)} messages")
            print(f"vs raw search:       {search_bytes:>10,} bytes")
            if search_bytes:
                print(f"Listing saving:      {1.0 - listing_bytes/search_bytes:.1%}")

            # Top 5 biggest savings
            print("\nTop 5 biggest savings:")
            for s in sorted(per_msg_stats, key=lambda x: x["saving"], reverse=True)[:5]:
                print(f"  {s['saving']:.0%}  {s['raw']:>6,}b->{s['norm']:>5,}b  {s['from'][:25]}  {s['subject'][:35]}")

            # Bottom 5 (least savings)
            print("\nBottom 5 (least savings):")
            for s in sorted(per_msg_stats, key=lambda x: x["saving"])[:5]:
                print(f"  {s['saving']:.0%}  {s['raw']:>6,}b->{s['norm']:>5,}b  {s['from'][:25]}  {s['subject'][:35]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real-world ts4k normalizer probe")
    parser.add_argument("email", help="Google email for the authenticated account")
    parser.add_argument("--url", default="http://localhost:51429/mcp", help="MCP server URL")
    parser.add_argument("--count", type=int, default=100, help="Number of messages to fetch")
    args = parser.parse_args()
    asyncio.run(run(args.email, args.url, args.count))
