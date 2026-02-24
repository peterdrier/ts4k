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
ts4k --> whatsapp-mcp (Python, MCP stdio) --> SQLite DB <-- Go bridge <-- WhatsApp
```

ts4k spawns the whatsapp-mcp server as a subprocess on demand. The Go bridge syncs messages from WhatsApp into a local SQLite database. ts4k reads from that database through the MCP server — no direct WhatsApp API access, no cloud services, everything local.

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

## Step 3: Install the MCP Server Dependencies

```bash
cd ../server
pip install -r requirements.txt
# or, if using uv:
uv sync
```

## Step 4: Verify the Database Exists

Make sure the SQLite database was created by the bridge:

```bash
ls ../bridge/store/messages.db
```

If the file exists, the bridge is working. You can also check message count:

```bash
sqlite3 ../bridge/store/messages.db "SELECT COUNT(*) FROM messages;"
```

## Step 5: Register the Source

Point ts4k at the whatsapp-mcp server directory:

```bash
ts4k src add w whatsapp mcp_cwd=/path/to/whatsapp-mcp/server
```

Replace `/path/to/whatsapp-mcp/server` with the actual path to the `server/` directory in your clone.

If you need a custom command to start the server (instead of the default `uv run main.py`):

```bash
ts4k src add w whatsapp mcp_cwd=/path/to/server server_command="python main.py"
```

Verify it's registered:

```bash
ts4k src list
```

## Step 6: Verify It Works

```bash
ts4k wn --source w
```

You should see your recent WhatsApp messages. If you get a connection error, make sure the `mcp_cwd` path is correct and the server dependencies are installed.

## No Authentication Needed

WhatsApp messages come from a local SQLite database — there are no OAuth tokens, API keys, or cloud credentials to manage. The Go bridge handles the WhatsApp device linking, and everything stays on your machine.

## Keeping Messages Current

The Go bridge needs to be running to receive new messages. Options:

- **Run manually** when you need fresh data: `cd bridge && ./whatsapp-bridge`
- **Run as a systemd service** (Linux) or startup task for continuous sync
- **Run on a schedule** — even without the bridge running, ts4k can still query whatever was last synced into the database

## Caching Note

WhatsApp messages come from a local database, so they're already fast to query. ts4k does **not** cache WhatsApp messages (caching is reserved for network-heavy sources like Gmail and O365). This means WhatsApp queries always read directly from the SQLite database through the MCP server.

## Troubleshooting

**"Connection refused" or "server not found"**
Check that `mcp_cwd` in your source config points to the `server/` directory (not the repo root). Verify deps are installed: `cd server && pip install -r requirements.txt`.

**"No messages found"**
Make sure the Go bridge has run at least once and the SQLite database exists at `bridge/store/messages.db`. Check that it has rows: `sqlite3 bridge/store/messages.db "SELECT COUNT(*) FROM messages;"`.

**QR code expired**
The QR code is valid for about 60 seconds. Run the bridge again to get a new one.

**"Linked device removed"**
WhatsApp occasionally unlinks inactive devices. Run the bridge again and re-scan the QR code.
