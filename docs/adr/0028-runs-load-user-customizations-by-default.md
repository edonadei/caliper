# Runs load the user's customizations by default

[0026](0026-attempts-never-see-account-connectors.md) cut every attempt off from
the user's own MCP setup. That left two gaps (#175). A skill that relies on a
hosted connector (Drive, Gmail, Calendar) couldn't be evaluated at all: 0026's
answer is "declare it under `mcp:`", but most of those connectors are remote
OAuth servers and a spec can't express OAuth
([0009](0009-mcp-secrets-interpolated-at-the-harness-boundary.md)). And someone
who wanted to see a skill behave in their own setup had to re-declare every
personal server first.

So an attempt now keeps the MCP servers its CLI would load by itself: the
account's hosted connectors **and** the servers in the user's own CLI config
(`~/.claude.json`, `~/.codex/config.toml`, `~/.hermes/config.yaml`). Both halves,
because "reproduce my setup" means both.

## Named for everything the harness loads

The switch is called **user customizations** (`--user-customizations`,
`user_customizations:`), not after MCP. It stands for everything a harness loads
from the user's own layer, which Claude Code calls *user scope*: MCP servers and
connectors today, and user skills, plugins, rules (`CLAUDE.md`) and settings to
follow (#177), each with its own decision (user skills compete with the skill
under test for activation). Naming the whole layer now means those join without
a rename. "Customizations" rather than "extensions" because rules and settings
change behaviour rather than add capabilities; "user" because that is the scope
the CLIs themselves use. `inherit-mcp` was the working name and was dropped as an
implementation word that also described only part of the layer.

## Loading them is the default

What a default run answers is "does my skill work in *my* agent, as I use it?"
Most people evaluate their own skills in their own setup, and caliper's own pitch
is "run your real agent". A clean-room default measured an agent nobody uses and
made every one of those users remember a flag.

The cost is that a default score depends on the machine that produced it, so
portability becomes the opt-out:

- `--no-user-customizations` isolates one run.
- `user_customizations: false` in a spec isolates every run of it, for an author who
  wants a portable number from anyone. The repo's smoke evals pin it: they check
  the backend, and the #129 probe tasks assert that an attempt sees only the
  declared servers.
- `user_customizations: true` says the skill needs the runner's setup, and
  `--user-customizations` turns it back on over a spec that pins `false`. The invocation
  wins, then the spec, then the default.

There is no CI detection. The same command measuring different things on a
laptop and in a pipeline is the kind of invisible difference 0026 cleaned up, and
a CI runner logged in with an API key has little to load anyway; the saved run
records what it did load.

Isolation is still what a comparison *between setups* needs: two backends, two
machines, or a number that leaves this one. The agent-facing methods
(`skills/evaluate-skill`, `skills/grill-skill`) carry that rule, and `compare`
warns on it.

It is not a security gate. An attempt's isolation kept a measurement clean and
was never a boundary ([0027](0027-an-attempt-is-not-a-security-boundary.md)):
running an eval already means trusting its skills with the user's filesystem.
A run that loads by default prints one dim line saying so; one that a flag or
the spec asked for prints a full warning naming its source. Neither prompts, so
`caliper run` stays usable non-interactively.

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

`--ablate` still only resolves declared names. A user's server has no name in
the spec, and ablation measures what the spec is answerable for
([0025](0025-ablation-covers-mcp-servers.md)'s "refuse rather than guess"). A
declared name stays the spec's even once ablated: `--ablate github` under the
default
also drops the user's own `github`, or the ablated arm would quietly get a server
back under the name it claims to have removed.

## How it got here

#176 first drafted this as an opt-in flag, then briefly as
`requires_inherited_mcp`, a spec precondition that refused a run without the flag
and could never turn inheritance on. Both kept isolation as the default. Neither
was released: the precondition made the author's intent something every runner
had to repeat, and the opt-in measured the clean agent by default when most runs
want the user's own.

## Where it does nothing

`pi` has no MCP by design ([0010](0010-pi-mcp-unsupported-by-design.md)). Loading
isn't refused there, unlike a declared `mcp:` block: it asks for "whatever my
setup has", and on pi that is nothing. The run records it as off, so `compare`
never reports a tool-environment difference that didn't exist. It warns only
when a flag or the spec asked for it; under the default it would warn on every
pi run.

The judge is never affected. A judge that can see connectors mistakes them for
the attempt's tools (0026).

## What a run records

`RunMeta.mcp_servers` keeps meaning the spec's surviving servers only, since
ablation pairing reads it. Two fields sit beside it:

- `user_customizations`, the setting that applied (a run saved before the field existed
  reads `false`: it was isolated);
- `loaded_user_customizations`, the loaded server names where the backend can see
  them. `claude-code` reads its `init` event; `codex` and `hermes` read the
  config the attempt ran with, and codex's hosted connectors count as one server,
  `codex_apps`. `None` means unknown, not none.

`compare` warns and never refuses. It warns when one side loaded and the
other didn't, saying how to match them, since the first diff against a run saved
before this default lands there; when both loaded but recorded different
servers; and when the two runs used different backends and either loaded,
because two CLIs never load the same setup, so part of that delta is the
setups rather than the harness. Two runs form an ablation pair only if they
agree on the setting, or the loaded servers would be an unrecorded difference
between the sides. `report` shows the
loaded servers in the run header, because a score measured with the machine's
setup otherwise reads exactly like an isolated one.

## Consequences

- A default score depends on the machine that produced it. The saved run says
  so, `report` shows it, and `compare` warns when that differs. A portable score
  is one flag or one spec line away.
- Local-scope servers Claude Code keys to a project path don't apply: the attempt
  runs in a fresh workdir, the same as a new project would.
- Loaded servers aren't preflighted the way declared stdio servers are.
  caliper can't start a hosted connector, and a personal server that fails to
  start is part of the setup the run was asked to reproduce.
- `codex_apps` is recorded only for a ChatGPT login whose config leaves the
  `apps` feature on; an API-key login carries no hosted connectors.
- When a source of loaded tools can't be listed (a ChatGPT login's codex
  plugins, hermes' `inherit_mcp_toolsets`), `loaded_user_customizations` is `None`
  rather than a partial list, so `compare` never treats an unlisted environment
  as empty or calls such a run a bare agent.
- Keeping *some* of the user's codex servers means editing their config rather
  than dropping whole tables, so codex's `config.toml` is now read with a TOML
  parser (`tomllib`, or `tomli` on Python 3.10) and written back whole. That
  handles every way TOML can spell a server, and an invalid user config stops
  the run with a clear error instead of reaching codex as broken input. The
  copy loses the user's comments and layout, which nothing reads.
- This supersedes 0026's default: attempts see the account's connectors unless
  isolated. 0026's mechanism is what isolation still does, and the smoke evals,
  pinned `user_customizations: false`, keep guarding it with the #129 probe tasks.
