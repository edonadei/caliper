# pi credentials resolve on the host

pi's `models.json` can name a credential instead of holding it: `!command` runs a
shell command and uses its output, and `$NAME` / `${NAME}` read an environment
variable. pi resolves these when it makes a request. In an attempt, that is inside
the isolated env: `HOME` is the attempt's own empty home and only a few variables
are passed through. So `!cat ~/.config/x/token` or `$MY_TOKEN`, which work when the
operator runs pi, resolved to nothing in the attempt (#219).

Caliper now resolves them itself, on the host, after it seeds `models.json` into
the attempt. That covers a provider's `apiKey` and every `headers` table (the
provider's, each model's, each `modelOverrides` entry's). It follows pi's own
rules: the same parsing and escapes, the same shell (`sh` on POSIX; Git Bash or a
`bash.exe` on `PATH` on Windows), and the same 10s limit. The result is written
back escaped (`$` → `$$`, a leading `!` → `$!`), so pi reads it literally. A
value the host can't resolve is left as written, and pi fails on it in the
attempt with its own error, as it would by hand.

This applies ADR 0009's principle to pi. Isolation strips ambient *state*, not
the credentials the operator configured, and those are read from the real
environment at the harness boundary. The syntax differs on purpose. 0009 limits
a spec to `${VAR}`, because caliper owns that grammar. Here the grammar is pi's,
in the operator's own file, so caliper accepts everything pi accepts, `!command`
included.

## Considered options

- **Stage what a helper needs into the isolated home** (the token file, the
  `~/.config` dir). Rejected: caliper can't know what an arbitrary command reads.
  Copying the operator's dotfiles into the attempt also brings back the ambient
  state that isolation removes.
- **Opt specific env vars through to the attempt.** Rejected: it fixes `$NAME`
  but not `!command`, and each passed-through variable is visible to the agent
  and to every tool it runs. A resolved key in `models.json` reaches only pi.
- **Leave it documented** (use a literal key or an absolute path). Rejected: a
  provider that works by hand shouldn't need a different config to be measured.

## Consequences

- The seeded `models.json` holds resolved secrets, so it is written `0600`, like
  every rewritten CLI config. pi's `auth.json` already holds keys verbatim.
- Credential commands run once per attempt, before the agent starts. A command
  that prompts (a password manager unlock, for example) prompts once per attempt.
  It runs even for a provider whose `auth.json` login means pi would never read
  the `apiKey`.
- pi re-runs a header command on every request. An attempt now sends the value
  from its start, so a token that expires within one attempt's runtime stops
  working partway through.
- `$NAME` reads the host environment. pi lets an `auth.json` credential's own
  `env` override that for headers; caliper doesn't.
- `auth.json` credentials are still resolved by pi inside the attempt.
