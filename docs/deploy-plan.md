# ts4k Deployment Plan: Bundled Connector Packaging

**Status:** Planning (pre-Phase 5)
**Date:** 2026-02-24
**Updated:** 2026-03-05 -- Connector table updated to reflect direct API adapters for Gmail/O365. Submodule and s6 scope revised.

---

## Problem

ts4k needs a deployment path that bundles everything users need. The scope of "external dependencies" has shrunk significantly since the original plan: two of three day-one adapters (Gmail, O365) now use direct API calls within the ts4k Python process, eliminating the need for external MCP bridges or CLI tools for those platforms.

### Connector Mix

| Connector | Type | Runtime | Lifecycle | External Process? |
|-----------|------|---------|-----------|-------------------|
| Gmail | Direct Google API (Python) | ts4k process | In-process | No |
| O365 | Direct Microsoft Graph API (httpx) | ts4k process | In-process | No |
| WhatsApp | Go MCP bridge (whatsapp-mcp-server) | Go binary | Long-running daemon | Yes |
| Telegram | TBD (likely direct via python-telegram-bot) | ts4k process | TBD | TBD |
| Slack | TBD (likely direct via slack_sdk) | ts4k process | TBD | TBD |
| Others | TBD | TBD | TBD | TBD |

The shift to direct APIs for Gmail and O365 means far fewer external processes to manage. Only WhatsApp (and potentially future adapters) requires a separate daemon.

---

## Recommended Approach: Single Container + s6-overlay

One Docker image containing ts4k and all its connectors, with [s6-overlay](https://github.com/just-containers/s6-overlay) managing the process mix.

### Why Single Container

- ts4k is the **orchestrator** — it shells out to CLIs and spawns MCP servers. Connectors are its dependencies, not independent services.
- Users get `docker run` (or a thin `docker compose up`), not a multi-service compose file.
- Simpler networking, shared filesystem, no inter-container coordination.
- Matches the LinuxServer.io pattern (Radarr, Sonarr, etc.) which uses exactly this approach for multi-process containers.

### Why s6-overlay

- Purpose-built for multi-process Docker containers.
- Handles long-running services (WhatsApp bridge), one-shot init scripts (config setup), and readiness dependencies between services.
- Clean process supervision with auto-restart for daemons.

**Note (2026-03-05):** With direct APIs for Gmail and O365, the only external process today is the WhatsApp bridge. s6-overlay may be overkill for supervising a single daemon. However, it remains the right choice if we add more external-process adapters in the future. For a WhatsApp-only deployment, a simpler approach (e.g., a shell wrapper or docker-compose with two services) could suffice.

### How Each Connector Type Fits

| Lifecycle | s6 handles as | Example |
|-----------|--------------|---------|
| In-process (direct API) | Not supervised — runs within ts4k Python process | Gmail, O365 |
| Long-running daemon | s6 longrun service with auto-restart | WhatsApp bridge |

---

## Container Structure

```
ts4k/
├── Dockerfile
├── docker-compose.yml          # Thin wrapper: volumes, ports, env
├── .env.example                # Template for user credentials
├── connectors/                 # Git submodules for external-process adapters
│   ├── whatsapp-mcp/           # → fork@<commit> (only external process today)
│   └── ...                     # Future external-process adapters
├── rootfs/
│   └── etc/
│       └── s6-overlay/s6-rc.d/
│           ├── init-config/        # oneshot: copy defaults, validate env
│           │   ├── type            # "oneshot"
│           │   └── up              # init script
│           ├── whatsapp-bridge/    # longrun: WhatsApp persistent session
│           │   ├── type            # "longrun"
│           │   └── run             # start script
│           └── ts4k-mcp/           # longrun: ts4k MCP server (optional)
│               ├── type            # "longrun"
│               └── run             # start script
└── src/ts4k/                   # ts4k itself
```

### Dockerfile Sketch

```dockerfile
FROM python:3.12-slim AS base

# --- s6-overlay ---
ARG S6_OVERLAY_VERSION=3.x.x
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz /tmp
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz /tmp
RUN tar -C / -Jxpf /tmp/s6-overlay-noarch.tar.xz && \
    tar -C / -Jxpf /tmp/s6-overlay-x86_64.tar.xz

# --- WhatsApp bridge (Go binary, only external-process connector) ---
COPY --from=golang:1.22-alpine AS wa-builder
# ... build whatsapp-mcp-server, copy binary to /usr/local/bin/whatsapp-mcp

# --- ts4k itself ---
COPY . /opt/ts4k
RUN pip install --no-cache-dir /opt/ts4k

# --- s6 service definitions ---
COPY rootfs/ /

# --- Config volume ---
VOLUME /config
ENV TS4K_CONFIG_DIR=/config

ENTRYPOINT ["/init"]  # s6-overlay entrypoint
```

### docker-compose.yml

```yaml
services:
  ts4k:
    build: .
    volumes:
      - ./config:/config          # Credentials, watermarks, state
      - ts4k-cache:/cache         # Preload cache (optional)
    ports:
      - "8400:8400"               # MCP server port (if exposed)
    env_file: .env
    restart: unless-stopped

volumes:
  ts4k-cache:
```

---

## Patched Forks Strategy

Use **git submodules** pinned to specific commits on our forks for external-process connectors:

```bash
git submodule add https://github.com/<fork>/whatsapp-mcp connectors/whatsapp-mcp
# Future external-process connectors would be added similarly
```

**Scope reduction (2026-03-05):** The original plan anticipated submodules for Gmail (google_workspace_mcp), WhatsApp, gog, and O365 connectors. With Gmail and O365 now using direct API calls within the ts4k Python process, only WhatsApp (and future external-process adapters) needs a submodule. The `google_workspace_mcp` fork and `gog` CLI are no longer used.

This ensures:
- `git clone --recurse-submodules` pulls everything in one command.
- Our patches are tracked and versioned.
- Updating a connector = update submodule pointer to new commit.
- Users always get the exact versions ts4k is tested against.

---

## User Experience

### First-time Setup

```bash
# 1. Clone everything
git clone --recurse-submodules https://github.com/peterdrier/ts4k
cd ts4k

# 2. Configure credentials
cp .env.example .env
# Edit .env: Gmail OAuth tokens, WhatsApp session config, etc.

# 3. Build and run
docker compose up -d

# 4. Use
docker exec ts4k ts4k whatsnew
# Or connect MCP client to localhost:8400
```

### Ongoing Use

```bash
# Update ts4k + all connectors
git pull --recurse-submodules
docker compose build && docker compose up -d
```

---

## Alternatives Considered

### Docker Compose Multi-Service (Rejected)

Each connector in its own container. Rejected because:
- CLI tools are awkward as their own containers (need `docker exec` sidecar pattern).
- ts4k shells out to / spawns these tools — they need to be co-located.
- More moving parts for users. Networking adds complexity.
- Doesn't match the mental model (ts4k + its dependencies, not a microservice fleet).

### No Docker / pip-only (Complementary)

`pip install ts4k` remains an option for users who want to manage connectors themselves. The Docker approach is the "batteries included" path. Both should work.

---

## Open Questions

1. **WhatsApp headless auth in Docker** — WhatsApp typically needs QR code scan on first run. Need to research headless/persistent session options inside a container.
2. **Credential management** — .env file vs mounted secrets vs interactive setup wizard on first run. Still unresolved.
3. **Image size budget** — Much improved. With direct APIs for Gmail/O365, the image no longer needs Node.js. Just Python + Go binary (WhatsApp) + s6. Multi-stage build should yield a reasonable image.
4. **Pre-built image vs build-from-source** — Decision 29 in the RIP says "no pre-built image" (credentials baked in risk). Revisit: credentials can be volume-mounted, so a pre-built image on Docker Hub/GHCR may be viable.
5. **Connector enable/disable** — Should users be able to skip connectors they don't need? (e.g., no WhatsApp). Environment variable flags or a config file.
6. **Is s6-overlay still justified?** — With only one external process (WhatsApp), a simpler supervision approach may suffice. Revisit when more external-process adapters are added.

---

## Relationship to RIP Phase 5

This plan expands on Phase 5's "Dockerfile + docker-compose.yml template" deliverable. The RIP envisions a template users build locally. This plan goes further: bundling connectors via submodules and using s6-overlay for process management, making the "template" into a complete, runnable deployment unit.

Phase 5 deliverables this replaces/extends:
- ~~Dockerfile + docker-compose.yml **template**~~ → Full Dockerfile + compose with s6-overlay + submodule connectors
- `pip install ts4k` / `uv tool install ts4k` → Still supported as the lightweight path
- README, setup guide → Must cover both Docker and pip paths
