# LAN MCP server (mcp-server.local)

Source: live inspection of the host over SSH, 2026-07-17. Plain-ASCII only.

## Host
- mDNS name `mcp-server.local` resolves to `192.168.2.216` (bare `mcp-server` does NOT
  resolve; the `.local` name works via avahi/nss-mdns). See memory `deck-curl-cares-lan-resolution`.
- Debian 13 (trixie), aarch64 (an ARM SBC). SSH user `madalone`.
- Deck's key `~/.ssh/id_ed25519.pub` is installed in `madalone@mcp-server:~/.ssh/authorized_keys`
  (added 2026-07-17), so passwordless SSH from the Deck works.

## What runs there: mcp-proxy fronting 5 stdio MCP servers over HTTP
- Tool: `mcp-proxy` v0.12.0 (sparfenyuk/mcp-proxy), installed via uv at
  `/home/madalone/.local/bin/mcp-proxy`.
- systemd unit `mcp-proxy.service` (system scope, User=madalone), Restart=always.
  ExecStart: `mcp-proxy --named-server-config /home/madalone/mcp-proxy/servers.json
  --port 9100 --host 0.0.0.0 --pass-environment`
  Log: `/home/madalone/mcp-proxy/mcp-proxy.log`. Config: `/home/madalone/mcp-proxy/servers.json`.
- Listens on `0.0.0.0:9100` (all interfaces, reachable from the whole LAN, NO auth).
  Port 9100 looks like a printer port but here it is the MCP proxy.

Each named server is exposed at BOTH transports:
- SSE:             `http://mcp-server.local:9100/servers/<name>/sse`
- Streamable HTTP: `http://mcp-server.local:9100/servers/<name>/mcp`   (preferred; SSE is deprecated)

Named servers (name in proxy = URL path segment):
| name         | package                        | notes |
|--------------|--------------------------------|-------|
| cool-vibes   | @coolver/home-assistant-mcp    | Home Assistant agent at http://192.168.2.211:8099 (HA_AGENT_KEY in config) |
| ha-ssh       | @fangjunjie/ssh-mcp-server     | remote shell to root@192.168.2.211:22222 (password in config) SHARP EDGE |
| gemini       | @rlabs-inc/gemini-mcp          | GEMINI_API_KEY in config; images to ~/gemini-images |
| simple-memory| simple-memory-mcp v1.28.1      | GraphQL memory store (see below) |
| context7     | @upstash/context7-mcp v3.2.3   | redundant: Deck already has Context7 via claude.ai |

## simple-memory: how to use
Registered in Claude Code at USER scope (all projects):
`claude mcp add --transport http --scope user simple-memory http://mcp-server.local:9100/servers/simple-memory/mcp`
Remove: `claude mcp remove simple-memory -s user`. Health: `claude mcp get simple-memory`.
Server data lives on the box at `/home/madalone/.simple-memory`.

Three tools: `memory-graphql` (main r/w), `export-memory`, `import-memory` (JSON backup/restore).

GraphQL schema (via `memory-graphql` tool, argument `{ "query": "..." }`):
- Queries:   `memories`, `memory`, `related`, `stats`
- Mutations: `store(content, tags)`, `update(hash, content, tags)`, `delete(hash, tag)`
- Memory record fields: hash, content, title, preview, tags(non-null), createdAt, updatedAt, relevance
- Memories are content-addressed by `hash`, tagged, with a `relevance` score and a `related` graph.

## Security notes (flagged to Miquel 2026-07-17)
- The proxy binds 0.0.0.0:9100 with NO authentication. Any device on the LAN can call every
  server, including `ha-ssh` which is a root shell on 192.168.2.211.
- Secrets sit in `servers.json` and in process cmdlines (visible to `ps` for any local user):
  root SSH password for .211, HA_AGENT_KEY, GEMINI_API_KEY.
- Not changed by us; his infra, his call. Options if he wants to harden: bind 127.0.0.1 and
  reach over SSH tunnel, or put an auth token / reverse proxy in front, or firewall port 9100.
