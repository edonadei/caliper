# The `pi` backend's `mcp:` refusal is permanent-by-design, not a pending slice

The `pi` coding agent (`@earendil-works/pi-coding-agent`) has **no MCP support,
and this is a deliberate stance of the tool** — its own README says "No MCP.
Build a CLI tool with a README (a skill), or an extension that adds MCP." There
is no `--mcp-config` flag, no `mcpServers` config, and no MCP dependency in the
package. So caliper's `mcp:` block cannot be materialized onto `pi` the way it is
onto `claude-code` (ADR 0008), and — unlike `codex`, which simply hasn't been
wired yet — pi will *never* honor it natively. We keep `pi`'s `supports_mcp` at
`False` and, rather than emit the generic "not supported yet" guard, give `pi` a
**tailored refusal** that names the permanence and points the spec author at
pi's own escape hatch: declare the server as a CLI tool the skill drives, or as a
pi extension. This is wired as a per-backend `mcp_unsupported_hint` the
centralized runner guard appends, so no backend can silently drop `mcp:`.

## Considered options

- **Ship a caliper-maintained pi extension that bridges `mcp:` onto pi.** This
  would make `mcp:` "work" on `pi`, but caliper would then own and version a pi
  extension, couple to pi's extension API, and re-introduce exactly the
  per-backend-realism divergence we otherwise avoid — a large surface to serve a
  tool whose authors deliberately excluded MCP. Rejected: the eval spec's `mcp:`
  is a portable capability declaration, not a mandate for caliper to synthesize
  the capability wherever it is missing.
- **Reuse the generic "not supported yet" guard for `pi`.** Rejected: "yet"
  is false for `pi` and misleads authors into expecting a later slice (and into
  filing "add MCP to pi"). The permanent case deserves its own message.
- **Silently run the spec with the declared servers absent.** Rejected for the
  same reason as ADR 0008: the attempt would test something other than what the
  spec claims.

## Consequences

- The `codex` refusal ("not yet", a real later slice) and the `pi` refusal
  ("never, by design") now read differently on purpose. `codex` leaves
  `mcp_unsupported_hint` unset and gets the generic message; `pi` sets it.
- If pi ever ships first-class MCP (or caliper's stance on a bridge changes),
  this ADR is superseded — flip `supports_mcp` and drop the hint.
