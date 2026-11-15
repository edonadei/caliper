# Antigravity names a lazy MCP call by its server and tool

By default `agy` loads an MCP server's tools lazily. The agent reads a tool's
schema from `~/.gemini/antigravity-cli/mcp/<server>/<tool>.json` and calls it
through one generic built-in, `call_mcp_tool`, passing `ServerName`, `ToolName`
and `Arguments`. Only a server marked `"eager": true` gets native
`mcp_<server>_<tool>` tool names. Left as it is, every MCP call in an
antigravity transcript would be named `call_mcp_tool`. An `expect:` judge
worded around the tool's name, and the per-server tally in the results, could
then not tell one server's call from another's.

We rename the call in the reader instead of changing how `agy` loads tools.
`_read_stream` turns a `call_mcp_tool` step into a `tool_use` turn named
`mcp_<server>_<tool>`, which is `agy`'s own eager spelling. Its input is the
call's `Arguments`. The backend declares `_` as its MCP tool separator, so
`mcp_server_of` attributes the call to its server as it does on hermes. Codex
is the precedent: it renames its MCP items to its `mcp__<server>__<tool>` form
in its reader too.

Marking every declared server eager would also produce those names, but it
would change what the agent sees. Every tool schema would sit in its context
from the first turn instead of being read on demand. That is a different
agent from the one the operator runs, and caliper reproduces the original
harness rather than adjusting it.

## Consequences

- A spec reads the same MCP call on antigravity whether `agy` loaded the tool
  lazily or eagerly.
- The agent's read of the tool's schema file stays in the transcript as an
  ordinary `read_file` turn.
- A `call_mcp_tool` step that names no server or tool keeps its own name.
