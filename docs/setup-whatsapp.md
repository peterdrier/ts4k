# WhatsApp Setup

This guide walks through connecting ts4k to your WhatsApp messages.

Unlike Gmail and O365, WhatsApp doesn't have an official API for personal accounts. ts4k connects through [whatsapp-mcp](https://github.com/peterdrier/whatsapp-mcp), which bridges WhatsApp message data from a local SQLite database into an MCP server that ts4k can query.

## Prerequisites

- Python 3.12+ with ts4k installed (`pip install -e .`)
- Python 3.11+ for the whatsapp-mcp server
- Go 1.21+ (for the WhatsApp bridge that populates the database)
- A WhatsApp account with an active phone

## Architecture

```
ts4k --HTTP MCP--> Go bridge --> SQLite DB
                       ^
                       └-- WhatsApp
```

The Go bridge syncs messages from WhatsApp into a local SQLite database and serves MCP over streamable HTTP at `http://127.0.0.1:18741/mcp`. ts4k talks to it directly — no subprocess, no direct WhatsApp API access, no cloud services, everything local.

The bridge also still runs a separate Python MCP server (`whatsapp-mcp-server/`) that speaks stdio and reads the same database. ts4k no longer uses it by default, but it remains the [rollback path](#rollback-to-the-stdio-server).

## Step 1: Clone the WhatsApp MCP Server

```bash
git clone https://github.com/peterdrier/whatsapp-mcp.git
cd whatsapp-mcp
```

> Use [the fork](https://github.com/peterdrier/whatsapp-mcp) — it includes fixes and improvements over the upstream repo.

## Step 2: Set Up the Go Bridge

The Go bridge connects to WhatsApp and syncs messages into a SQLite database.

```bash
cd bridge
go build -o whatsapp-bridge .
./whatsapp-bridge
```

On first run, it displays a QR code in the terminal. Scan it with your phone:

1. Open WhatsApp on your phone
2. Go to **Settings > Linked Devices > Link a Device**
3. Scan the QR code

The bridge starts syncing messages into `bridge/store/messages.db`. Keep it running (or set it up as a service) to keep the database current.

## Step 3: Verify the Database Exists

Step 2 left you in `whatsapp-mcp/bridge`; these paths are relative to it.

Make sure the SQLite database was created by the bridge:

```bash
ls store/messages.db
```

If the file exists, the bridge is working. You can also check message count:

```bash
sqlite3 store/messages.db "SELECT COUNT(*) FROM messages;"
```

## Step 4: Find the Bridge Key

On first run the bridge mints a shared secret at `bridge/store/api_token` and never prints it again. It is an HMAC key, not a password: ts4k signs each request with it, and the key itself never crosses the wire.

```bash
ls -l store/api_token
```

## Step 5: Register the Source

```bash
ts4k src add w whatsapp bridge_token_file=/path/to/whatsapp-mcp/bridge/store/api_token
```

`bridge_url` defaults to `http://127.0.0.1:18741/mcp`. Set it only if the bridge listens elsewhere:

```bash
ts4k src add w whatsapp \
  bridge_url=http://127.0.0.1:18741/mcp \
  bridge_token_file=/path/to/bridge/store/api_token
```

If ts4k runs on a different machine from the bridge, copy the key to a file on that machine and point `bridge_token_file` at it, or export `WHATSAPP_API_TOKEN` in that environment:

```bash
# on the ts4k host
install -m 600 /dev/null ~/.config/ts4k/wa_api_token
# paste the key into it, then:
ts4k src add w whatsapp bridge_token_file=~/.config/ts4k/wa_api_token
```

Prefer either of those over `bridge_token=<key>` on the command line: an argument lands in shell history and is visible in process listings, and this key grants read access to the whole archive wherever the bridge is reachable. `bridge_token` still works for throwaway setups, and ts4k redacts it from `src add` / `src list` output, but neither of those helps once it is in `~/.bash_history`.

Note that the bridge binds loopback only — reaching it from another host needs a tunnel, not a config change here.

Verify it's registered:

```bash
ts4k src list
ts4k auth --check          # reports whether the bridge key resolves
```

## Step 6: Verify It Works

```bash
ts4k wn --source w
```

You should see your recent WhatsApp messages.

## Authentication

There is no OAuth, no cloud credential, and no login flow. Two separate things authenticate:

- **WhatsApp → bridge**: the QR pairing from Step 2. Lives in the bridge, done once.
- **ts4k → bridge**: challenge-response over the shared key. Each request goes out unsigned, the bridge answers `401` with a single-use nonce, and ts4k re-sends an HMAC over the method, path, nonce and body — over a fresh connection. Nothing replayable is ever sent, so capturing a request buys an attacker only that one request.

## Rollback to the stdio server

The Python MCP server (`whatsapp-mcp-server/`) still exists and still reads the same database. To point ts4k back at it:

```bash
ts4k src add w whatsapp transport=stdio \
  mcp_cwd=/path/to/whatsapp-mcp/whatsapp-mcp-server \
  server_command="uv run main.py"
```

This re-adds the source with `transport=stdio`, which makes ts4k spawn the Python server as a subprocess again. `bridge_url` and the key are ignored in that mode. Switch back with `ts4k src add w whatsapp bridge_token_file=...` (no `transport` key, or `transport=http`).

Install its dependencies first if you have not run it before:

```bash
cd ../whatsapp-mcp-server && uv sync   # from bridge/; use the repo root otherwise
```

## Keeping Messages Current

On the default HTTP transport the bridge **is** the MCP endpoint, so it has to be running for every query — not just to receive new messages. With it stopped, ts4k gets connection-refused rather than stale-but-usable results. Run it as a systemd service (Linux) or a startup task.

```bash
cd bridge && ./whatsapp-bridge
```

Only the [stdio rollback](#rollback-to-the-stdio-server) can read the database while the bridge is down: there the Python server opens the SQLite file itself, so queries return whatever was last synced.

## Caching Note

WhatsApp messages come from a local database, so they're already fast to query. ts4k does **not** cache WhatsApp messages (caching is reserved for network-heavy sources like Gmail and O365). This means WhatsApp queries always read live from the bridge, which reads the SQLite database — there is no ts4k-side copy to go stale, and equally nothing to answer from while the bridge is stopped.

## Troubleshooting

**"Connection refused"**
The bridge is not running, or is not listening on `bridge_url`. Start it (`cd bridge && ./whatsapp-bridge`) and check the port with `curl -i http://127.0.0.1:18741/mcp -X POST` — a healthy bridge answers `401` with a `WWW-Authenticate: HMAC-SHA256` header.

**HTTP 401 from the bridge**
ts4k has no key, or the wrong one. `ts4k auth --check` says whether one resolves at all; if it does, confirm `bridge_token_file` points at the `store/api_token` of the bridge you are actually talking to. The key is regenerated if that file is deleted, which invalidates every client holding the old one.

**"No messages found"**
Make sure the Go bridge has run at least once and the SQLite database exists at `bridge/store/messages.db`. Check that it has rows: `sqlite3 bridge/store/messages.db "SELECT COUNT(*) FROM messages;"`.

**QR code expired**
The QR code is valid for about 60 seconds. Run the bridge again to get a new one.

**"Linked device removed"**
WhatsApp occasionally unlinks inactive devices. Run the bridge again and re-scan the QR code.
