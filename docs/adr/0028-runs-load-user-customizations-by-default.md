# Runs load the user's customizations by default

[0026](0026-attempts-never-see-account-connectors.md) cut every attempt off from
the user's own MCP setup, which left two gaps (#175). A skill that relies on a
hosted OAuth connector (Drive, Gmail) couldn't be evaluated at all, because a
spec can't express OAuth
([0009](0009-mcp-secrets-interpolated-at-the-harness-boundary.md)). And
reproducing your own setup meant re-declaring every personal server.

So an attempt now loads the **user customizations** its CLI would load by
itself: the servers in the user's own CLI config and the account's hosted
connectors. The judge never does: a judge that sees connectors mistakes them for
the attempt's tools (0026).

## Loading them is the default

A default run answers "does my skill work in *my* agent, as I use it?" That is
what most people evaluate, and caliper's pitch is "run your real agent". A
clean-room default measured an agent nobody uses.

The cost is that a default score depends on the machine, so portability is the
opt-out: `--no-user-customizations` for one run, `user_customizations: false` for
every run of a spec. `user_customizations: true` says a skill needs the
runner's setup. The flag wins, then the spec, then the default. The repo's smoke
evals pin `false`, so the #129 probe tasks keep guarding isolation.

There is no CI detection: the same command measuring different things on a
laptop and in a pipeline is the invisible difference 0026 cleaned up, and the
saved run records what was loaded anyway.

It is not a security gate. Isolation kept a measurement clean and was never a
boundary ([0027](0027-an-attempt-is-not-a-security-boundary.md)). The run prints
a notice (one dim line by default, a full warning when a flag or spec asked) and
never prompts, so `caliper run` stays usable non-interactively.

## Named for the whole user layer

"User customizations" names everything a harness loads from the user's own
layer, which Claude Code calls *user scope*: MCP servers and connectors today,
and user skills, plugins, rules and settings to follow (#177), each with its own
decision. "Customizations", not "extensions", because rules and settings change
behaviour rather than add capabilities. The working name `inherit-mcp` was
dropped as an implementation word naming only part of the layer.

## The spec wins a name clash

With a declared `mcp:` block the attempt gets the union, and a declared server
replaces the user's server of the same name: the spec is committed and means the
same thing everywhere, the user's config doesn't. A declared name stays the
spec's even once ablated, or `--ablate github` would quietly get the user's
`github` back. `--ablate` still names only declared servers
([0025](0025-ablation-covers-mcp-servers.md)). Each backend applies this to the
isolated copy of the config, never the real file.

## Consequences

- A default score depends on the machine. The saved run records what was loaded
  (`None` when a source such as codex plugins can't be listed), `report` shows
  it, and `compare` warns when two runs differ, including two backends compared
  with customizations loaded (see [results](../results.md)).
- `pi` has no MCP ([0010](0010-pi-mcp-unsupported-by-design.md)): a run there is
  recorded as isolated, with a warning only when someone asked to load.
- Loaded servers aren't preflighted like declared ones: a hosted connector
  can't be started, and a broken personal server is part of the setup being
  reproduced.
- Keeping some of the user's codex servers means editing their `config.toml`,
  so it is now parsed (`tomllib`, `tomli` on 3.10) rather than line-edited.
- Supersedes 0026's default. 0026's mechanism is what isolation still does.
