# MCP preflight gates modern-only servers on the backend

MCP 2026-07-28 drops the `initialize` handshake, and a server that speaks only
that revision rejects it. Preflight still sends `initialize` first and sends
`server/discover` only after a rejection. A legacy server sees exactly the
exchange it saw before, and a server that speaks both revisions answers
`initialize`, so every backend can connect to it. The spec's discover-first
order is for a client choosing how to talk; preflight only asks whether this
backend's agent can connect. Claude Code still talks 2026-07-28 to a server that
speaks both, so for such a server preflight checks the older exchange, as it
did before this decision. *(Amended: on a backend that speaks 2026-07-28,
preflight now probes `server/discover` first — see below.)*

A modern-only server passes only when the backend declares
`speaks_modern_mcp`. A live modern server is not proof the agent can use it:
codex opens stdio servers with `initialize` only, and hermes falls forward to
`server/discover` only for some `initialize` errors. An
`UnsupportedProtocolVersion` reply (-32022) proves the server is alive, not that
it shares a version, so it stops the run too, as does a discovery result
that doesn't list 2026-07-28. A modern `tools/list` must carry the
`resultType`, whole-number `ttlMs` and `cacheScope` the revision requires, and
no `error` key beside its result: Claude Code drops a server's tools otherwise.

The flag is per backend, not per installed version. Caliper assumes a current
`claude-code` (2.1.292 or later, where 2026-07-28 is the stdio default) rather
than probing the CLI version on every run.

## Amendment: preflight opens a server the way the backend's agent does

Checking the older exchange let a broken server through. A dual-era server
whose 2026-07-28 `tools/list` lacks `resultType`, `ttlMs` or `cacheScope`
answered `initialize`, so it passed preflight, and then Claude Code dropped its
tools: the attempt scored as a task failure instead of stopping the run.

On a backend with `speaks_modern_mcp`, preflight now follows the stdio
transport's fallback, as Claude Code does. It sends `server/discover` first. A
discovery result is checked in 2026-07-28, an `UnsupportedProtocolVersion`
reply stops the run, and any other error falls back to `initialize` on the same
process. A server that stays silent past the 3-second discovery wait, exits, or
sends something unreadable is restarted before the fallback, since a read
from the abandoned probe may still be waiting on its output.

The other backends still send `initialize` first, because their agents do.
A legacy server on those backends still never sees `server/discover` unless
it rejects `initialize`.
