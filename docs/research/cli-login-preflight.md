# CLI login preflight

Research note for [#190](https://github.com/edonadei/caliper/issues/190).
Measured on 2026-10-08 on macOS with Claude Code 2.1.293, codex-cli 0.145.0,
pi 0.87.1 and Hermes 0.18.0. [`login_probe.py`](login_probe.py) reruns the
status-check rows against throwaway homes. It never touches your real
credentials and never calls a model.

## Findings

- Every supported CLI has a login-status check that answers in under two
  seconds without a model call. Most of them only confirm that a credential
  exists.
- Only `pi auth check` tells a dead OAuth login from a refreshable one, because
  it attempts the refresh. Codex can do the same through its experimental
  app-server protocol. Claude Code and Hermes cannot without a model call.
- No CLI validates an API key without calling the provider. A bogus key reports
  as logged in everywhere.
- A tiny prompt is not a cheap liveness check. A live `claude -p` took 13 to 15
  seconds and loaded the user's whole Claude configuration.
- The costly case today is the judge, not the agent. A lapsed judge login
  records `judge_error` on every attempt after paying for its agent run
  ([#245](https://github.com/edonadei/caliper/issues/245)).

## What each status check catches

The dead OAuth login has an expired access token and a refresh token the
provider rejects.

| CLI | Check | No login | Dead OAuth login | Bogus API key | Time |
|---|---|---|---|---|---|
| Claude Code | `claude auth status` | exit 1 | logged in, exit 0 | logged in, exit 0 | 0.2 to 0.6 s |
| Codex | `codex login status` | exit 1 | logged in, exit 0 | logged in, exit 0 | 0.05 to 0.2 s |
| Codex | app-server `account/read` with `refreshToken: true` | no account | no account | `apiKey` account | 0.1 to 1.1 s |
| pi | `pi auth check --provider <p> --json` | exit 1, `not_ready` | exit 2, `invalid` | exit 0, `ready` | 0.2 to 0.6 s |
| pi | the same with `--no-refresh` | exit 1 | exit 0, `ready` | exit 0, `ready` | 0.2 s |
| Hermes | `hermes auth status <p>` | prints `logged out`, exit 0 | not measured | logged in | 1 to 1.7 s |

Hermes is not in the probe script. Its source documents `auth status` as
"read-only by contract", so it never refreshes
(`get_codex_auth_status` in `hermes_cli/auth.py`). It reports the state a
credential pool last recorded, so it catches a dead login only after a refresh
has already failed once. For an API-key provider it reports whether a key is
configured. It exits 0 whether or not you are logged in, so a caller has to
read its output.

A probe in a fresh `HERMES_HOME` ran Hermes' first-run installer and blocked
for more than 30 seconds on an install lock. Probe the real `~/.hermes`, with
stdin closed and a timeout.

On the machine used for this note, `pi auth check --provider anthropic` caught
a real lapsed login. The access token expired on 2026-09-25 and the refresh
failed. The default `openai-codex` provider reported `ready`.

## Refreshable credentials

An expired access token does not mean the login is unusable. Claude Code and
Codex both report an expired token as logged in, which is correct while the
refresh token still works. Telling the two cases apart takes a refresh attempt:

- pi refreshes by default in `auth check`. With `--no-refresh` it only checks
  that a credential exists.
- Codex refreshes in app-server `account/read` when `refreshToken` is true. A
  failed refresh returned no account and left `auth.json` in place.
- Claude Code has no refresh command. It refreshes only when it calls the API.
  After a `claude -p` call failed the refresh, `claude auth status` reported
  `loggedIn: false`.
- Hermes `auth status` never refreshes. `hermes auth refresh <p>` does, but it
  changes the credential pool and needs a target when the pool holds more than
  one credential.

A refresh has to run against the account that Caliper seeds attempts from,
never an attempt's copy. Hermes ships a repair for "forked" grants of
single-use Codex refresh tokens (`SINGLE_USE_REFRESH_POOL_PROVIDERS`), which
suggests that a refresh in a copy can invalidate the original login. This note
did not reproduce that.

## Why a tiny prompt is not the check

A dead login fails before inference, so a prompt against it costs nothing.
It is slow, though:

| Prompt | Dead login | Time |
|---|---|---|
| `claude -p "Reply with OK"` | expired OAuth | 1.6 s |
| `claude -p "Reply with OK"` | bogus API key | 4.5 s |
| `codex exec "Reply with OK"` | expired OAuth | 5.1 s |
| `codex exec "Reply with OK"` | bogus API key | 22.8 s, from websocket retries |

Against a live login it is a real agent run. `claude -p "Reply with OK" --model
haiku` took 13.1 seconds, read 72,000 context tokens from the user's
`CLAUDE.md`, skills and hooks, replied with an unrelated message, and wrote a
file. With `--strict-mcp-config --tools ""` it still took 15 seconds and did
not reply with only "OK". That costs more than the failure it would prevent.

## What a lapsed login costs today

The agent side already stops. The first attempt whose CLI reports a login
failure raises a configuration error and ends the run
([#199](https://github.com/edonadei/caliper/pull/199) for Claude Code, and the
shared `AUTH_MARKERS` in `caliper/harness/refusal.py` for the others). Up to
`--workers` attempts, 4 by default, can already be running. They lose their
setup time, not model spend, because a dead login fails before inference.

The judge side does not stop. `EvalJudge` stops the run only on
`PromptFailureKind.MODEL_UNAVAILABLE`. Calling the claude-code harness's
`run_prompt` with dead credentials gave these results:

- A bogus API key returned `PromptFailureKind.AUTH`, which the judge records as
  `judge_error`.
- An expired OAuth login returned no failure. The text `Failed to authenticate:
  OAuth session expired and could not be refreshed` went to verdict parsing as
  the judge's answer.

Each attempt pays for its agent run and then records `judge_error`.

## Recommendations

### Where the check runs

Run it in the runner's `before_attempts` callback, where `check_judge_cli`
already runs. It fires after spec validation and skill and server resolution,
so an invalid spec still reports its own error first. Check the agent backend.
Check the judge backend when a task has `expect:`. Probe each distinct backend
once.

Each backend declares its probe and its message
([ADR 0020](../adr/0020-a-backend-declares-its-chores-rather-than-performing-them.md))
behind one method beside `prompt_cli_missing`. A probe that times out or cannot
run counts as unknown and does not stop the run. Only a definite "not logged
in" stops it. Filed as [#246](https://github.com/edonadei/caliper/issues/246).

### Interactive login

Do not build it now. pi has no login subcommand. You log in with `/login`
inside its terminal UI. Hermes 0.18 marks `hermes login` deprecated in favor of
`hermes auth add`. One launch flow cannot treat the backends the same way. The
preflight also runs before any attempt, so rerunning `caliper run` after you log
in loses nothing. Revisit if users ask for it.

### CI and agent-driven runs

Never start or wait for a login flow. Run probes with stdin closed and a
timeout, and print the exact command:

| Backend | Command |
|---|---|
| `claude-code` | `claude auth login` |
| `codex` | `codex login`, or `codex login --device-auth` without a browser |
| `pi` | `pi`, then `/login` |
| `hermes` | `hermes auth add <provider>`, then `hermes model` |

Caliper still tells users to run `hermes login` in
`HermesHarness.cli_unavailable_message`, the hermes setup hint and
`docs/backends.md`. #246 replaces it.

### Source account

Probe the configuration that attempts are seeded from:

- Claude Code: the user's own config and Keychain entry.
- Codex: the `.codex` that `seed_files` copies, passed as `CODEX_HOME`.
  `codex login status` honors `CODEX_HOME`, and the harness seeds from
  `~/.codex`. A user with `CODEX_HOME` set would otherwise probe one account
  and run another. [#112](https://github.com/edonadei/caliper/issues/112)
  settles which one is the source.
- pi: `~/.pi/agent`. A refresh there writes the new token into the real
  `auth.json`, which every attempt then copies.
- Hermes: `~/.hermes`.

### Not filed

- A tiny-prompt probe. It is slow and acts on the user's configuration.
- Codex app-server `account/read`. It detects a dead ChatGPT login in about 1
  second, but its protocol is marked experimental. Revisit if lapsed ChatGPT
  logins come up in practice.
- An interactive login flow, for the reasons above.
