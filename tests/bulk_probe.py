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
from ts4k.core.format import estimate_size, format_listing
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
    use_tokens: bool,
):
    # Set up token counter if requested
    tok_fn = None
    if use_tokens:
        from token_counter import count_tokens
        test = count_tokens("hello")
        if test is None:
            print("WARNING: Token counting unavailable (no ANTHROPIC_API_KEY or API error). Falling back to bytes only.\n")
        else:
            tok_fn = count_tokens
            print(f"Token counting enabled (smoke test: 'hello' = {test} tokens)\n")

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

            # Measure listing output
            listing_out = format_listing(all_entries, "pipe")
            listing_bytes = len(listing_out.encode())
            listing_saving = 1.0 - (listing_bytes / search_bytes_total) if search_bytes_total else 0

            print("\n--- Search complete ---")
            print(f"Pages:      {page_num}")
            print(f"Messages:   {len(all_entries)}")
            print(f"Raw search:   {search_bytes_total:>10,} bytes ({estimate_size(search_bytes_total)})")
            print(f"Pipe listing: {listing_bytes:>10,} bytes ({estimate_size(listing_bytes)})")
            print(f"Listing saving: {listing_saving:.1%}")
            print(f"Time:       {search_elapsed:.1f}s")
            if page_num > 0:
                print(f"Avg/page:   {search_elapsed / page_num:.2f}s")

            if not fetch_bodies or not all_entries:
                return

            # --- Phase 2: Fetch and normalize message bodies ---
            print(f"\n--- Fetching {len(all_entries)} message bodies ---")

            raw_total = 0
            norm_total = 0
            raw_tokens_total = 0
            norm_tokens_total = 0
            token_counted = 0
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

                    # Token counting
                    raw_tok = None
                    norm_tok = None
                    if tok_fn:
                        raw_tok = tok_fn(msg_text)
                        formatted_text = norm_body + "\n" + "\n".join(
                            v for v in norm_hdrs.values() if isinstance(v, str)
                        )
                        norm_tok = tok_fn(formatted_text)
                        if raw_tok is not None and norm_tok is not None:
                            raw_tokens_total += raw_tok
                            norm_tokens_total += norm_tok
                            token_counted += 1

                    if (i + 1) % 25 == 0 or i == len(all_entries) - 1:
                        pct = 1.0 - (norm_total / raw_total) if raw_total else 0
                        line = f"  [{i+1}/{len(all_entries)}] raw={estimate_size(raw_total)} norm={estimate_size(norm_total)} saving={pct:.0%}"
                        if token_counted > 0:
                            tok_pct = 1.0 - (norm_tokens_total / raw_tokens_total) if raw_tokens_total else 0
                            line += f"  tok_saving={tok_pct:.0%} ({token_counted} counted)"
                        print(line)

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
            print("BULK PROBE RESULTS")
            print(f"{'=' * 60}")
            print(f"Query:           {query}")
            print(f"Messages found:  {len(all_entries)}")
            print(f"Errors:          {errors}")
            print(f"Expansions:      {expansions} (normalized > raw)")
            print(f"Raw total:       {raw_total:>12,} bytes ({estimate_size(raw_total)})")
            print(f"Normalized:      {norm_total:>12,} bytes ({estimate_size(norm_total)})")
            saving = 1.0 - (norm_total / raw_total) if raw_total else 0
            print(f"Byte saving:     {saving:.1%}")

            if token_counted > 0:
                tok_saving = 1.0 - (norm_tokens_total / raw_tokens_total) if raw_tokens_total else 0
                delta = tok_saving - saving
                print(f"\nRaw tokens:      {raw_tokens_total:>12,}")
                print(f"Norm tokens:     {norm_tokens_total:>12,}")
                print(f"Token saving:    {tok_saving:.1%}")
                print(f"Byte vs token:   {delta:+.1%}  ({'tokens save more' if delta > 0 else 'bytes save more'})")
                print(f"Token-counted:   {token_counted}/{len(all_entries)} messages")

            print(f"\nSearch time:     {search_elapsed:.1f}s ({search_elapsed/max(page_num,1):.2f}s/page)")
            print(f"Fetch time:      {fetch_elapsed:.1f}s ({fetch_elapsed/max(len(all_entries),1):.2f}s/msg)")
            print(f"Total time:      {total_elapsed:.1f}s")

            if len(all_entries) > 0:
                if token_counted > 0:
                    # Use real token counts for cost estimate
                    raw_tok_per_msg = raw_tokens_total / token_counted
                    norm_tok_per_msg = norm_tokens_total / token_counted
                    print("\nCost estimate (real tokens):")
                    print(f"  Avg raw tok/msg:   {raw_tok_per_msg:,.0f}")
                    print(f"  Avg norm tok/msg:  {norm_tok_per_msg:,.0f}")
                    print(f"  At $3/MTok:        ~{1_000_000 / raw_tok_per_msg:,.0f} msgs/$1 (raw)")
                    print(f"  At $3/MTok:        ~{1_000_000 / norm_tok_per_msg:,.0f} msgs/$1 (normalized)")
                else:
                    # Fall back to byte-based estimate
                    cost_per_msg_bytes = raw_total / len(all_entries)
                    msgs_per_dollar = 1_000_000 / (cost_per_msg_bytes * 0.000003)  # rough token cost
                    print(f"\nAvg raw/msg:     {estimate_size(int(cost_per_msg_bytes))}")
                    print(f"At $3/MTok:      ~{msgs_per_dollar:,.0f} msgs/$1 (raw, byte estimate)")
                    norm_per_msg = norm_total / len(all_entries)
                    msgs_per_dollar_norm = 1_000_000 / (norm_per_msg * 0.000003)
                    print(f"At $3/MTok:      ~{msgs_per_dollar_norm:,.0f} msgs/$1 (normalized, byte estimate)")


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
    parser.add_argument("--tokens", action="store_true", help="Count tokens via Anthropic API (requires ANTHROPIC_API_KEY)")
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
        args.tokens,
    ))
