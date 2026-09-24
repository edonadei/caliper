# `--inherit-mcp` is an opt-in invocation flag

[0026](0026-attempts-never-see-account-connectors.md) cut every attempt off from
the user's own MCP setup, and that stays the default. It left two gaps (#175). A
skill that relies on a hosted connector (Drive, Gmail, Calendar) can't be
evaluated at all: 0026's answer is "declare it under `mcp:`", but most of those
connectors are remote OAuth servers and a spec can't express OAuth
([0009](0009-mcp-secrets-interpolated-at-the-harness-boundary.md)). And someone
who wants to see a skill behave in their own setup has to re-declare every
personal server first.

So `caliper run --inherit-mcp` lets an attempt keep the MCP servers its CLI would
load by itself: the account's hosted connectors **and** the servers in the user's
own CLI config (`~/.claude.json`, `~/.codex/config.toml`, `~/.hermes/config.yaml`).
Both halves, because "reproduce my setup" means both, and a flag for one half
would bring the same question back for the other.

It is an invocation flag, not a spec field, for the same reason the engine is
([0004](0004-engine-is-a-runtime-axis-not-a-spec-field.md)): what gets inherited
depends on the machine running the eval, not on the spec. It is not a security
gate either. An attempt's isolation keeps a measurement clean and was never a
boundary ([0027](0027-an-attempt-is-not-a-security-boundary.md)), so the run
prints a notice rather than asking for confirmation. Typing the flag is the
consent, and `caliper run` stays usable non-interactively.

## Merged with `mcp:`, the spec winning a name clash

With a declared `mcp:` block the attempt gets the union. A spec's server replaces
a user's server of the same name: the spec is committed and means the same thing
on every machine, and the user's config is whatever that machine holds. Each
backend enforces this in the isolated copy of the config and never touches the
real file:

- `claude-code` drops `--strict-mcp-config` and still passes `--mcp-config`, after
  removing any clashing name from the seeded `.claude.json`'s `mcpServers`. Taking
  the clash out of the copy means caliper doesn't depend on how the CLI orders
  its scopes.
- `codex` leaves off its `-c features.apps=false -c features.plugins=false`
  overrides and keeps the user's `[mcp_servers.*]` tables, except the clashing
  ones. The model strip ([0012](0012-cli-harnesses-copy-cli-config-verbatim.md))
  still applies. The hosted apps surface as a server named `codex_apps`, so a
  spec that declares that name, ablated or not, keeps `features.apps=false`.
- `hermes` keeps the user's `mcp_servers` (and `inherit_mcp_toolsets`) with the
  declared servers merged on top.

`--ablate` still only resolves declared names. An inherited server has no name in
the spec, and ablation measures what the spec is answerable for
([0025](0025-ablation-covers-mcp-servers.md)'s "refuse rather than guess"). A
declared name stays the spec's even once ablated: `--ablate github --inherit-mcp`
also drops the user's own `github`, or the ablated arm would quietly get a server
back under the name it claims to have removed.

## A spec can require it, never grant it

Some evals measure nothing without the runner's setup: a Drive skill run in
isolation scores 0%, which reads as a broken skill rather than a missing flag. So
a spec may say `requires_inherited_mcp: true`, and `caliper run` refuses without
`--inherit-mcp`, before any attempt is paid for, the way an `mcp:` spec is
refused on a backend that can't honor it.

The field is a precondition, not a switch. A spec that could turn inheritance on
would hand the runner's accounts to whoever wrote it (a teammate, a git source)
without the runner typing anything, which is the consent the flag exists to
carry. A boolean rather than a list of required connectors: which connectors an
attempt gets is only visible once it runs (claude-code's `init` event), too late
to refuse cheaply, and connector names differ between backends.

## Where it does nothing

`pi` has no MCP by design ([0010](0010-pi-mcp-unsupported-by-design.md)). The flag
isn't refused there, unlike a declared `mcp:` block: it asks for "whatever my
setup has", and on pi that is nothing. The run warns and records the flag as off,
so `compare` never reports a tool-environment difference that didn't exist. A
spec that *requires* inherited MCP is refused there, since the requirement can't
be met.

The judge is never affected. A judge that can see connectors mistakes them for
the attempt's tools (0026).

## What a run records

`RunMeta.mcp_servers` keeps meaning the spec's surviving servers only, since
ablation pairing reads it. Two fields sit beside it:

- `inherit_mcp`, whether the flag was on;
- `inherited_mcp_servers`, the inherited server names where the backend can see
  them. `claude-code` reads its `init` event; `codex` and `hermes` read the
  config the attempt ran with, and codex's hosted connectors count as one server,
  `codex_apps`. `None` means unknown, not none.

`compare` warns and never refuses. It warns when one side ran with the flag and
the other didn't, and when both did but recorded different inherited servers.
Two runs form an ablation pair only if they agree on the flag, or the inherited
servers would be an unrecorded difference between the sides. `report` shows the
inherited servers in the run header, because a score measured with the machine's
setup otherwise reads exactly like an isolated one.

## Consequences

- A score run with `--inherit-mcp` depends on the machine that produced it. The
  saved run says so, and `compare` warns when that differs.
- Local-scope servers Claude Code keys to a project path don't apply: the attempt
  runs in a fresh workdir, the same as a new project would.
- Inherited servers aren't preflighted the way declared stdio servers are.
  caliper can't start a hosted connector, and a personal server that fails to
  start is part of the setup the run was asked to reproduce.
- `codex_apps` is recorded only for a ChatGPT login whose config leaves the
  `apps` feature on; an API-key login carries no hosted connectors.
- Keeping *some* of the user's codex servers means editing their config rather
  than dropping whole tables, so codex's `config.toml` is now read with a TOML
  parser (`tomllib`, or `tomli` on Python 3.10) and written back whole. That
  handles every way TOML can spell a server, and an invalid user config stops
  the run with a clear error instead of reaching codex as broken input. The
  copy loses the user's comments and layout, which nothing reads.
- This supersedes 0026's "a skill that relies on an account connector can't be
  evaluated through it", for runs that opt in. The default is unchanged, and the
  #129 probe tasks in the smoke evals still guard it.
