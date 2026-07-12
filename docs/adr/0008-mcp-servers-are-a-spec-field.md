# MCP servers are a spec field, not a CLI flag or runtime axis

An eval spec gains a top-level `mcp:` block declaring the MCP servers the
agent-under-test may use. It is a mapping keyed by server name; for this slice a
server is a **local stdio** process (`command`, `args`, `env`), and the presence
of `command` is what marks it stdio — there is no `type:` discriminator yet.
We put this in the spec — as a **top-level sibling of `sandbox:`** — rather than
behind a `caliper run` flag or in the runtime engine selection, because an MCP
server is a **capability granted to the agent-under-test for the eval**: part of
the run environment, not a property of the invocation. `sandbox:` takes
capabilities away (forbidden files, PATH) where `mcp:` adds them; both describe
the environment an attempt runs in, so both are authored in the spec that
travels with the eval. This is the mirror image of ADR 0004: the *engine* is a
swept runtime axis because it is the variable under test, whereas the tools the
agent is given are a fixed fact about the eval and belong in the file.

It is deliberately **not** a sub-field of `skill:`. The skill is optional
(`skill.path` may be absent for a bare-agent eval), but the MCP servers are
granted to the *agent*, not to a SKILL.md — a bare-agent run can still declare
`mcp:` (the end-to-end test is exactly that: `skill: {}` + `mcp:`). Nesting the
block under `skill:` would force tool config to hang off an empty `skill:` with
no `path:`, which reads as incoherent. Framing it as "a dependency of the skill"
was the misleading phrasing that made nesting tempting; the accurate frame is a
run-environment capability, sibling to `sandbox:`.

Scope of this slice: **stdio transport only, `claude-code` backend only.** This
is the tracer bullet establishing the backend-agnostic `mcp:` surface every
later slice builds on (remote/HTTP servers; other backends).

## Considered options

- **A `--mcp-config` CLI flag.** Rejected: it would make the same spec test a
  different thing depending on invocation, and force every runner of the eval to
  re-supply the dependency out of band — the opposite of a spec that travels
  self-contained.
- **A `type: stdio` discriminator now.** Rejected for this slice: stdio is the
  only transport that exists, so requiring `type` is boilerplate on every entry.
  Transport is inferred from shape (`command` ⇒ stdio); slice 2 adds `url` and
  infers HTTP. Adding an optional `type` later is non-breaking, so nothing is
  foreclosed.
- **A list of `- name:` server objects.** Rejected: MCP servers are an unordered
  set identified by name (like `sandbox:`), not an ordered sequence of work (like
  `tasks:`). A mapping keyed by name makes duplicate/empty names unrepresentable,
  gives uniqueness for free, and is one-to-one with the CLI's own `.mcp.json`, so
  materialization needs no transposition.

## Consequences

- **Backends that don't implement MCP must refuse, not silently drop it.** A spec
  with `mcp:` run on `codex`/`pi`/`hermes` would run with the declared tools
  absent — a misleading pass/fail. Support is modeled as a per-backend capability
  (`supports_mcp`, default `False`; `claude-code` overrides `True`); the run seam
  hard-errors before any attempt when `spec.mcp` is non-empty and the chosen
  backend lacks the capability. Each future slice flips one backend's flag, and
  the guard relaxes automatically — there is no backend-name list to maintain.
- **`caliper validate` owns the `mcp:` grammar.** Server names must match
  `^[A-Za-z0-9_-]+$` (so the `mcp__<name>__<tool>` transcript handle is
  well-formed), each entry is `extra="forbid"` (a mistyped `commnd:` or a
  not-yet-supported `url:` errors clearly), `command` is a required non-empty
  string, `args` is `list[str]`, `env` is `dict[str,str]`.
- **The transcript needs no parser change.** The stream parser already captures
  `tool_use` generically, so an MCP call surfaces as
  `mcp__<server>__<tool>` for free — `expect:`/`assert:` judges and the cheat
  detector reason about it with no new code.
