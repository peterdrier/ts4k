"""P4 — Bulk Probe: paginate years of history for one contact.

Validates:
  1. Pagination works (page_token chaining across many pages)
  2. Total volume / time for historical backload
  3. Rate limiting / throttling viability
  4. Normalizer handles variety of real-world content

Usage:
  python tests/bulk_probe.py EMAIL --contact "someone@example.com" --years 2
  python tests/bulk_probe.py EMAIL --query "from:someone@example.com" --max-pages 50
"""

import argparse
import asyncio
import os
import re
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from ts4k.core.normalize import normalize, normalize_headers
from ts4k.core.format import estimate_size
from ts4k.adapters.gmail import parse_search_response, parse_message_response


# Regex to find page_token in upstream response
_PAGE_TOKEN_RE = re.compile(r"page_token='([^']+)'")


async def run(
    email: str,
    server_url: str,
    query: str,
    max_pages: int,
    page_size: int,
    fetch_bodies: bool,
    throttle: float,
):
    async with streamablehttp_client(server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"Connected. Query: {query}")
            print(f"Max pages: {max_pages}, page size: {page_size}, throttle: {throttle}s")
            if not fetch_bodies:
                print("(headers only — use --fetch-bodies to also fetch and normalize content)")
            print()

            all_entries = []
            page_token = None
            page_num = 0
            search_bytes_total = 0
            t_start = time.monotonic()

            # --- Phase 1: Paginate search results ---
            while page_num < max_pages:
                page_num += 1
                args = {
                    "user_google_email": email,
                    "query": query,
                    "page_size": page_size,
                }
                if page_token:
                    args["page_token"] = page_token

                result = await session.call_tool("search_gmail_messages", args)
                text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
                search_bytes_total += len(text.encode())

                entries = parse_search_response(text, "g")
                all_entries.extend(entries)

                # Extract next page token
                token_match = _PAGE_TOKEN_RE.search(text)
                page_token = token_match.group(1) if token_match else None

                print(f"  Page {page_num}: {len(entries)} messages (total: {len(all_entries)}, token: {'yes' if page_token else 'no'})")

                if not page_token:
                    print("  No more pages.")
                    break

                if throttle > 0:
                    await asyncio.sleep(throttle)

            t_search = time.monotonic()
            search_elapsed = t_search - t_start

            print(f"\n--- Search complete ---")
            print(f"Pages:      {page_num}")
            print(f"Messages:   {len(all_entries)}")
            print(f"Search bytes: {search_bytes_total:,}")
            print(f"Time:       {search_elapsed:.1f}s")
            if page_num > 0:
                print(f"Avg/page:   {search_elapsed / page_num:.2f}s")

            if not fetch_bodies or not all_entries:
                return

            # --- Phase 2: Fetch and normalize message bodies ---
            print(f"\n--- Fetching {len(all_entries)} message bodies ---")

            raw_total = 0
            norm_total = 0
            errors = 0
            expansions = 0  # messages where normalized > raw

            for i, entry in enumerate(all_entries):
                raw_id = entry["raw_id"]
                try:
                    msg_result = await session.call_tool(
                        "get_gmail_message_content",
                        {"user_google_email": email, "message_id": raw_id},
                    )
                    msg_text = "\n".join(c.text for c in msg_result.content if hasattr(c, "text"))
                    raw_bytes = len(msg_text.encode())
                    raw_total += raw_bytes

                    parsed = parse_message_response(msg_text, "g", raw_id)
                    norm_body = normalize(parsed["body"])
                    norm_hdrs = normalize_headers({
                        "from": parsed.get("from", ""),
                        "subject": parsed.get("subject", ""),
                        "date": parsed.get("date", ""),
                    })

                    norm_bytes = len(norm_body.encode()) + sum(
                        len(v.encode()) for v in norm_hdrs.values() if isinstance(v, str)
                    )
                    norm_total += norm_bytes

                    if norm_bytes > raw_bytes:
                        expansions += 1

                    if (i + 1) % 25 == 0 or i == len(all_entries) - 1:
                        pct = 1.0 - (norm_total / raw_total) if raw_total else 0
                        print(f"  [{i+1}/{len(all_entries)}] raw={estimate_size(raw_total)} norm={estimate_size(norm_total)} saving={pct:.0%}")

                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        print(f"  [{i+1}] ERROR: {e}")

                if throttle > 0:
                    await asyncio.sleep(throttle)

            t_fetch = time.monotonic()
            fetch_elapsed = t_fetch - t_search
            total_elapsed = t_fetch - t_start

            print(f"\n{'=' * 60}")
            print(f"BULK PROBE RESULTS")
            print(f"{'=' * 60}")
            print(f"Query:           {query}")
            print(f"Messages found:  {len(all_entries)}")
            print(f"Errors:          {errors}")
            print(f"Expansions:      {expansions} (normalized > raw)")
            print(f"Raw total:       {raw_total:>12,} bytes ({estimate_size(raw_total)})")
            print(f"Normalized:      {norm_total:>12,} bytes ({estimate_size(norm_total)})")
            saving = 1.0 - (norm_total / raw_total) if raw_total else 0
            print(f"Overall saving:  {saving:.1%}")
            print(f"Search time:     {search_elapsed:.1f}s ({search_elapsed/max(page_num,1):.2f}s/page)")
            print(f"Fetch time:      {fetch_elapsed:.1f}s ({fetch_elapsed/max(len(all_entries),1):.2f}s/msg)")
            print(f"Total time:      {total_elapsed:.1f}s")

            if len(all_entries) > 0:
                cost_per_msg_bytes = raw_total / len(all_entries)
                msgs_per_dollar = 1_000_000 / (cost_per_msg_bytes * 0.000003)  # rough token cost
                print(f"\nAvg raw/msg:     {estimate_size(int(cost_per_msg_bytes))}")
                print(f"At $3/MTok:      ~{msgs_per_dollar:,.0f} msgs/$1 (raw)")
                norm_per_msg = norm_total / len(all_entries)
                msgs_per_dollar_norm = 1_000_000 / (norm_per_msg * 0.000003)
                print(f"At $3/MTok:      ~{msgs_per_dollar_norm:,.0f} msgs/$1 (normalized)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P4 Bulk Probe — paginate history")
    parser.add_argument("email", help="Google email for authenticated account")
    parser.add_argument("--url", default="http://localhost:51429/mcp", help="MCP server URL")
    parser.add_argument("--contact", help="Contact email to search for (builds from: query)")
    parser.add_argument("--query", help="Raw Gmail search query (overrides --contact)")
    parser.add_argument("--years", type=int, default=1, help="How many years back to search")
    parser.add_argument("--max-pages", type=int, default=100, help="Max pages to fetch")
    parser.add_argument("--page-size", type=int, default=100, help="Messages per page")
    parser.add_argument("--fetch-bodies", action="store_true", help="Also fetch and normalize each message")
    parser.add_argument("--throttle", type=float, default=0.1, help="Delay between requests (seconds)")
    args = parser.parse_args()

    if args.query:
        query = args.query
    elif args.contact:
        query = f"from:{args.contact} newer_than:{args.years * 365}d"
    else:
        query = f"newer_than:{args.years * 365}d"

    asyncio.run(run(
        args.email, args.url, query,
        args.max_pages, args.page_size,
        args.fetch_bodies, args.throttle,
    ))
