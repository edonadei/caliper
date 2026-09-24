# Inherited MCP is opt-in, set by the spec or the invocation

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

A spec can turn it on by default (`inherit_mcp: true`), and the invocation
overrides that either way (`--inherit-mcp` / `--no-inherit-mcp`); see below. It
is not a security gate. An attempt's isolation keeps a measurement clean and was
never a boundary ([0027](0027-an-attempt-is-not-a-security-boundary.md)), so the
run prints a notice rather than asking for confirmation, and `caliper run` stays
usable non-interactively.

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

## The spec sets the default, the invocation overrides it

Some evals measure nothing without the runner's setup: a Drive skill run in
isolation scores 0%, which reads as a broken skill rather than a missing flag.
Making every run of such a spec remember a flag is the same trap, so the spec
says `inherit_mcp: true` and that is the default for every run of it.
`--no-inherit-mcp` turns it off for one run (an isolated comparison, say) and
`--inherit-mcp` turns it on for a spec that doesn't ask for it.

What gets inherited still depends on the machine, which is why the engine stays
off the spec ([0004](0004-engine-is-a-runtime-axis-not-a-spec-field.md)). The
difference is that "this eval needs the runner's connectors" is a fact about the
eval, while "run it on codex" is not. The machine-dependence is handled where it
always is: the saved run records what was inherited and `compare` warns when two
runs differ.

A spec turning it on hands the runner's accounts to whatever skills it installs,
without the runner typing anything. That is the trust 0027 already says running
an eval takes: a skill runs as the user, with the user's filesystem. The notice
at the start of the run names the spec as the source and says how to turn it
off, so it is never silent.

It was first drafted (#176) as `requires_inherited_mcp`, a precondition that refused a
run without the flag and could never turn inheritance on. It was replaced before
release: it made the author's intent something every runner had to repeat.

## Where it does nothing

`pi` has no MCP by design ([0010](0010-pi-mcp-unsupported-by-design.md)). The flag
isn't refused there, unlike a declared `mcp:` block: it asks for "whatever my
setup has", and on pi that is nothing. The run warns and records the flag as off,
so `compare` never reports a tool-environment difference that didn't exist. The
same holds when the spec turned it on.

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
- When a source of inherited tools can't be listed (a ChatGPT login's codex
  plugins, hermes' `inherit_mcp_toolsets`), `inherited_mcp_servers` is `None`
  rather than a partial list, so `compare` never treats an unlisted environment
  as empty or calls such a run a bare agent.
- Keeping *some* of the user's codex servers means editing their config rather
  than dropping whole tables, so codex's `config.toml` is now read with a TOML
  parser (`tomllib`, or `tomli` on Python 3.10) and written back whole. That
  handles every way TOML can spell a server, and an invalid user config stops
  the run with a clear error instead of reaching codex as broken input. The
  copy loses the user's comments and layout, which nothing reads.
- This supersedes 0026's "a skill that relies on an account connector can't be
  evaluated through it", for runs that opt in. The default is unchanged, and the
  #129 probe tasks in the smoke evals still guard it.
