# On Windows, a hermes attempt home is a standalone profile under the hermes root

ADR 0005 runs every hermes attempt in an isolated `HERMES_HOME` inside the
attempt's temp dir. On Windows (hermes v0.22) that home cannot boot. Hermes keeps
its installed dependencies once per *root*, under `<root>/installs/`, and the
Windows launcher refuses to start from a home whose root has none committed
(`no dependency environment is committed for this install`). A temp-dir home is
its own root, so every attempt failed in under a second (#302). It failed the
same way with the parent's full environment, so no forwarded variable fixes it.

On Windows, `isolated_home/.hermes` is now a directory junction to a fresh
**named profile**, `%LOCALAPPDATA%\hermes\profiles\caliper-*`. Its `config.yaml`
pins `gateway.standalone: true` from the profile's first file onward. Everything
caliper writes still goes through `isolated_home/.hermes`. Hermes is handed the
junction's target as `HERMES_HOME`. Once the attempt has been read, caliper stops
the attempt's gateway and removes the junction, then the profile. macOS and Linux
keep the temp-dir home unchanged.

Why each part:

- **Under the root**: hermes places a home at or under its root (by resolved
  path) on the root's dependencies, which is what lets it boot.
- **A named profile, standalone**: `hermes -z` runs through a gateway it starts
  for the active home. By default that gateway multiplexes every profile on the
  host. It then tries to reserve the default profile, which the user's own
  running gateway already owns, and exits (`Gateway runtime already owns
  profile`). Hermes retired `gateway.multiplex_profiles: false`. The only
  remaining opt-out is a named profile marked `gateway.standalone`, and that
  marker also makes the user's multiplexer skip the profile instead of adopting
  it. A probe dir under `profiles/` without it was adopted within the 30-second
  rescan.
- **A junction**: unlike a symlink it needs no privilege, and it keeps the
  attempt's files at `isolated_home/.hermes`. The seeding, the `cli_config`
  rewrite, the user layer and the session reader therefore needed no change.
- **Released after the read, not in `_command`'s cleanup**: a timed-out
  attempt's recovery export and the MCP inventory both read the home after the
  process exits.

Neutralization (ADR 0005) is unchanged: only `auth.json`, `.env` and the
rewritten `config.yaml` are seeded, and `--ignore-rules` stays. The profile is
fresh per attempt, so attempts stay independent and parallel-safe.

## Considered options

- **The user's real `HERMES_HOME`** (the issue's suggestion). Rejected: persona,
  memory and every past session would be in play, `sessions export -` assumes a
  home holding exactly one session, and parallel attempts would share one store.
- **A home under the root but outside `profiles/`.** It boots, and the user's
  gateway ignores it, but its own gateway multiplexes and collides with the
  user's (above) whenever the host has a named profile.
- **Refuse hermes on Windows.** Rejected: the profile route works end to end.

## Consequences

- **The attempt writes inside the user's hermes root** for its lifetime: a
  profile named `caliper-*`, visible in `hermes profile list` while it runs.
  ADR 0005's "never mutates the user's real hermes home" holds only on
  macOS/Linux.
- **Release costs seconds**: `hermes gateway stop` waits for the attempt's
  gateway to drain (about 7s). It runs only when a gateway was started.
- **Leftovers are possible and swept.** A file still held at release time
  survives. Each attempt home carries a `.caliper-attempt` marker that is
  deleted last, and a later attempt removes marked homes older than a day. A
  user's own `caliper-*` profile carries no marker and is never touched.
- **Seeding and the user layer read `%LOCALAPPDATA%\hermes`** on Windows, where
  hermes keeps its home, instead of `~/.hermes`.
- **Attempts get `USERPROFILE`** (the isolated home) and the real
  `LOCALAPPDATA`: Python on Windows ignores `HOME`, and hermes finds its root
  through `LOCALAPPDATA`.
- **Coupled to hermes internals** (the dependency-root rule and the standalone
  marker). A hermes release that changes either breaks Windows attempts loudly,
  as infra errors, not silently.
