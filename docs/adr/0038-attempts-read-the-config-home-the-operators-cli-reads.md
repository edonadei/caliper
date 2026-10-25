# Attempts read the config home the operator's CLI reads

Each backend CLI lets the operator move its config home with a variable:
`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_DIR`, `HERMES_HOME`. The
judge runs in the operator's environment, so it already honoured them. Attempts
did not: seeding, the `cli_config` rewrite, the user layer and codex plugins all
read the default home (`~/.claude`, `~/.codex`, `~/.pi/agent`, `~/.hermes`). An
operator with a second account in `CODEX_HOME` got attempts on one login and the
judge on another, or attempts with no login at all (#112).

Every backend now declares its variable and its default home
(`config_home_env`, `config_home` on `CliHarness`). The user's copy of any path
inside the attempt's config home resolves under the variable when it is set,
otherwise under the default, so attempts and the judge use the same account. A
variable that is set but doesn't name a directory stops the run before the first
attempt and names the variable. Seeding from it would only show up later, as a
login failure on every attempt.

The attempt itself never inherits the operator's value. Codex, pi and hermes
attempts get the variable pointed at their own isolated home. claude-code
attempts get no `CLAUDE_CONFIG_DIR` and stay on the default layout under the
isolated `HOME`.

## Considered options

- **Keep reading the default home, and document the variables as unsupported.**
  Rejected: the judge would keep running on a different account than the
  attempts, and that mismatch is hard to spot.
- **A Caliper-specific variable (`CALIPER_CODEX_HOME`, …).** Rejected: it would
  be one more name per backend for something the CLI already names, and leaving
  it unset would bring back the mismatch.
- **Codex only, as #112 asked.** Rejected: the other three backends have the
  same gap, and one rule for all four keeps the harnesses consistent.

## Consequences

- **claude-code is the exception for the attempt env.** With
  `CLAUDE_CONFIG_DIR` set, the CLI reads `.claude.json` from inside the config
  dir instead of beside it. It also suffixes its macOS Keychain entry with the
  first 8 hex digits of the sha256 of the dir (`Claude Code-credentials-<hash>`).
  Caliper reads the operator's files and Keychain entry under those rules, and
  writes the attempt's copy to the default layout, which the isolated `HOME`
  already gives it.
- **Hermes on Windows** still places attempt profiles under the default root
  (ADR 0037). Only the seed source follows `HERMES_HOME`.
- A `~` at the start of the variable expands, as pi and hermes expand it.
