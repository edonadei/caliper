# MCP preflight gates modern-only servers on the backend

MCP 2026-07-28 drops the `initialize` handshake, and a server that speaks only
that revision rejects it. Preflight still sends `initialize` first and sends
`server/discover` only after a rejection. A legacy server sees exactly the
exchange it saw before, and a server that speaks both revisions answers
`initialize`, which every backend's agent can use. The spec's discover-first
order is for a client choosing how to talk; preflight only asks whether this
backend's agent can connect.

A modern-only server passes only when the backend declares
`speaks_modern_mcp`. A live modern server is not proof the agent can use it:
codex opens stdio servers with `initialize` only, and hermes falls forward to
`server/discover` only for some `initialize` errors. An
`UnsupportedProtocolVersion` reply (-32022) proves the server is alive, not that
it shares a version, so it stops the run too. A modern `tools/list` must carry
the `ttlMs` and `cacheScope` caching hints the revision requires: Claude Code
drops a server's tools without them.

Known gap: the flag is per backend, not per installed version, so `claude-code`
older than 2.1.292 is still declared modern.
