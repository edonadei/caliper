# Backends

Caliper runs every skill through a real CLI agent, so every backend can load and
run a skill the way a user would. This page covers setup for each one, how
`--model` and `--judge-model` pick an engine, and which backends support `mcp:`
and `history:`.

For the short version, see [Choosing an engine](../README.md#choosing-an-engine)
in the README.

## Selecting an engine

The engine is not stored in the spec. `--model` picks the engine that runs the
skill, `--judge-model` picks the one that grades it. `--model` defaults to
`claude-code`, and `--judge-model` to the `--model` backend. Each flag accepts a `backend:model` pair, a bare backend name, or
a bare model name, each optionally followed by `@<effort>`:

```bash
# Backend and model together
caliper run my-skill.eval.yaml --model codex:gpt-5.6-sol

# Backend only (that backend's default model)
caliper run my-skill.eval.yaml --model codex

# Model only (backend stays claude-code)
caliper run my-skill.eval.yaml --model claude-fable-5

# Codex runs and grades (no --judge-model)
caliper run my-skill.eval.yaml --model codex

# Select the judge engine independently
caliper run my-skill.eval.yaml --model codex --judge-model claude-code:claude-haiku-4-5-20251001

# Name an effort level (backend default model, low effort)
caliper run my-skill.eval.yaml --model claude-code@low
caliper run my-skill.eval.yaml --model codex:gpt-5.6-sol@high --judge-model claude-code:sonnet@low
```

Accepted backends: `claude-code`, `codex`, `pi`, `hermes`, `antigravity`,
`opencode` (aliases: `claude` and `claude_code` → `claude-code`, `agy` →
`antigravity`).

With no `--judge-model` the judge runs on the backend of the model being
evaluated (`--model`), on that CLI's default model rather than the `--model`
model itself. `--judge-model` picks any other
pairing, such as a Codex skill with a Claude judge. To compare engines fairly,
pass the same `--judge-model` to each run; otherwise each engine grades itself,
and `caliper compare` warns that the judges differ
([ADR 0034](adr/0034-the-judge-follows-the-skill-backend-by-default.md)).

### Effort level

`@<effort>` sets the effort the CLI runs at, for the attempts or the judge.
`opus@low` and `opus@max` are different engines, so a run records the effort as
part of its engine ([ADR 0045](adr/0045-effort-is-part-of-the-engine-target.md)).
Each backend takes the words its own CLI uses:

| Backend | Passed as | Levels |
|---|---|---|
| `claude-code` | `--effort` | `low`, `medium`, `high`, `xhigh`, `max` |
| `codex` | `-c model_reasoning_effort=` | whatever codex accepts (codex checks it) |
| `pi` | `--thinking` | `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `antigravity` | `--effort` | `low`, `medium`, `high`, `xhigh`, `max` |
| `hermes` | `--reasoning` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| `opencode` | `--variant` | provider-specific (opencode checks it) |

- A level the backend doesn't list stops the run before the first attempt
  (exit `2`), naming the levels it takes.
- `@` is split off only before an effort word, so a model id like
  `claude-sonnet-4@20250514` keeps its `@`. After a bare backend name
  (`codex@<word>`) any word is the effort.
- With no `@<effort>`, the CLI runs at its own default, or at the level your
  config pins when user customizations are on.
- The judge follows the `--model` backend but not its effort: name one in
  `--judge-model` to set it.

There is no direct-API backend. To run against API-priced billing, configure one
of these CLIs with an API key (for example `ANTHROPIC_API_KEY` or
`OPENAI_API_KEY`).

When a run stops because the agent's or the judge's CLI is not logged in, it
exits `2` with a `Not logged in` error that says how to log in. In an
interactive terminal, `caliper run` also offers to run that backend's login
command (`claude auth login`, `codex login`, `hermes model`, `agy` or
`opencode auth login`, or
`caliper login chatgpt` when you [signed Caliper in to
ChatGPT](#running-on-your-chatgpt-plan)) and reruns the
eval once the login succeeds. pi logs in only from its own terminal UI, so it
gets no offer.

### Which account attempts use

Attempts copy your login and config from the same place the CLI itself reads
them, so attempts and the judge use the same account. Each `~/...` path on this
page means the default location. If you set the CLI's own variable, Caliper
reads from there instead:

| Backend | Variable | Default |
|---|---|---|
| `claude-code` | `CLAUDE_CONFIG_DIR` | `~/.claude` (and `~/.claude.json`) |
| `codex` | `CODEX_HOME` | `~/.codex` |
| `pi` | `PI_CODING_AGENT_DIR` | `~/.pi/agent` |
| `hermes` | `HERMES_HOME` | `~/.hermes` (`%LOCALAPPDATA%\hermes` on Windows) |
| `antigravity` | none | `~/.gemini` (login in the OS keyring) |
| `opencode` | `XDG_CONFIG_HOME`, `XDG_DATA_HOME` | `~/.config/opencode` (config), `~/.local/share/opencode` (logins) |

If the variable is set to something that isn't a directory, the run stops before
the first attempt (exit `2`). With `CLAUDE_CONFIG_DIR` set, `.claude.json` is
read from inside that dir, and on macOS the login comes from the Keychain entry
the CLI keeps for that dir. Each attempt still runs on its own isolated copy
([ADR 0038](adr/0038-attempts-read-the-config-home-the-operators-cli-reads.md)).

### What gets recorded

Each saved run's `RunMeta` records the engine that was actually used: the skill
`backend`/`model`, and the `judge_backend`/`judge_model` that graded it. Results
stay traceable even though the spec doesn't pin an engine.

- The skill `model` is the concrete model the agent reported running, wherever
  the backend reports it (Hermes' session export), not the one you asked for. A
  default-model run records the resolved model instead of a bare "default".
- If the backend reports a different model than `--model` named, the run records
  what actually ran and prints a warning. If attempts report different models,
  the run records the most common one and warns.
- The `judge_model` likewise comes from the judge's own output when it reports
  one (the `claude-code` judge's JSON does), even when you named a model, so
  `--judge-model claude-code:opus` records the id `opus` resolved to.
  Otherwise it's the model you named.
- `judge_model` stays empty for an `assert:`-only run, where no LLM judge ran.
- `effort` is the level the agent ran at, and `effort_source` says how Caliper
  knows it: `reported` when the CLI wrote it down (codex, pi, opencode and
  hermes do, even for a level your config set), `named` when it came from
  `@<effort>` (claude-code and antigravity report none), or `config` when no
  `@` was given and your copied claude-code `settings.json` pins `effortLevel`.
  Both are empty when the CLI ran at its own default. A reported level that
  differs from the named one wins and prints a warning, as a model does.
- `judge_effort` is the level `--judge-model` named. The judge runs in your
  real CLI home, so Caliper doesn't read back what it ran at.
- When `--judge-model` names no model, the judge uses its CLI's own default
  model, like the skill does. The `codex` judge reads your real
  `~/.codex/config.toml` (under `CODEX_HOME` when set), so a `model =` pinned there applies to it, while an
  attempt drops that pin. After `caliper login chatgpt`, the `codex` judge
  runs on your ChatGPT plan, like the attempts.
- A spec with `expect:` stops before its first attempt if the judge's CLI
  isn't installed, rather than recording a `judge_error` on every attempt.

## Claude Code

Install and authenticate the `claude` CLI. `--model claude-code` uses your
existing Claude Code auth, with no extra configuration. On macOS the Keychain
entry wins over `~/.claude/.credentials.json`, as it does for the CLI itself, so
a stale file left next to a valid Keychain login doesn't break attempts.

Each attempt gets a copy of that login **without its refresh token**, so an
attempt can never refresh it. A refresh inside an attempt would be lost with
the attempt's home, and two at once with the same refresh token revoke your
login (#324). Instead, when the login is within five minutes of expiring, Caliper
refreshes it once on your machine, with one short `claude -p` call, before the
attempt starts. Concurrent attempts and runs are then safe. If an attempt
outlives its access token, it fails as not logged in rather than refreshing.

An expired OAuth session or invalid API key stops the run as a configuration
error. Run `claude auth login` and retry the eval. Caliper reads these
failures from the CLI's error output, not from an agent discussing authentication.

The CLI ships skills of its own (`claude-api`, `debug` and others) that stay
discoverable even in an isolated run. Caliper reads which ones the CLI exposed
from its `init` event, and reports any the agent reaches for as
**built-in skills**: shown in the report and in `builtin_activated`, never scored
against `activates:`. The other backends don't list their built-in skills yet,
so they record `builtin_activated: null`.

## Codex

```bash
npm install -g @openai/codex
codex login
```

`--model codex` calls `codex exec`. Caliper runs the `codex` on your `PATH`, the
same one your shell runs, and falls back to the binary bundled in the Codex
desktop app only when `PATH` has none. Set `CODEX_CLI_PATH` to force a specific
binary.

### Running on your ChatGPT plan

Each attempt normally gets a copy of your `~/.codex/auth.json`. With a ChatGPT
login that copy is fragile: when an attempt refreshes its token, the new token
is deleted with the attempt, and parallel attempts all try to refresh with the
same one. Your own Codex login can stop working until you run `codex login`
again.

To run codex on a Plus or Pro plan, sign Caliper in to ChatGPT itself:

```bash
caliper login chatgpt
```

This opens your browser. Allow Caliper to use your plan. The login is stored in
`~/.config/caliper/chatgpt.json` (under `XDG_CONFIG_HOME` when set, and
`%APPDATA%\caliper` on Windows), not in `~/.codex`. From then on:

- Caliper refreshes the login itself, one refresh at a time, before an attempt
  starts.
- Each attempt and the judge get a short-lived access token, valid for at least
  the attempt's `--timeout`. They never see your `auth.json`.
- A refresh OpenAI refuses stops the run with `Not logged in` and points at
  `caliper login chatgpt`.

`caliper logout chatgpt` revokes the session and deletes the file. Codex runs
then use your Codex CLI login again.

Some things don't work on a plan login:

- Plan usage rejects some Responses features, including hosted connectors,
  `tool_search` and `temperature`. An eval that needs them fails there.
- An access token lasts an hour. Attempts can't refresh while they run, so one
  with a longer `--timeout` may see its token expire.
- On a remote machine, run `caliper login chatgpt` locally and copy
  `chatgpt.json` over SSH. Leave the remote `host-id` file in place.
- A fresh CI runner can't keep a refreshed login, so CI should use an API key.

See [ADR 0039](adr/0039-caliper-owns-the-chatgpt-plan-login.md).

## pi

```bash
npm install -g @earendil-works/pi-coding-agent
pi   # then authenticate (e.g. /login for a subscription provider, or set the provider API key)
```

`--model pi` runs `pi --print --mode json` and installs the declared skills under
pi's config home, where pi discovers them.

- Caliper never passes pi's `--skill` flag, because it *preloads* the skill.
  Discovery is pi's default behavior, which is why pi also has `--no-skills`.
- It reuses your `~/.pi/agent` `auth.json`, `settings.json` and `models.json`,
  so custom providers from `models.json` work in attempts. Caliper resolves
  `!command` and `$NAME` credentials in `models.json` (each `apiKey` and
  `headers` value) on the host before the attempt starts. It follows pi's own
  rules, so `!cat ~/.config/x/token` or `$MY_TOKEN` gets the value it has when
  you run pi yourself, even though the attempt has its own empty `HOME` and
  only a few variables. A command runs once per attempt. A value that doesn't
  resolve on the host is left as written, and pi reports it.
  See [ADR 0040](adr/0040-pi-credentials-resolve-on-the-host.md).
  The `:model` half of `--model pi:<model>` overrides pi's configured default.
  pi's own `<model>:<level>` shorthand is refused: write `pi:<model>@<level>`,
  so the run records the level.
- pi's built-in default provider is `google`, so `--model pi` with no model
  relies on your pi config to resolve a provider you're authenticated for.
- Set `PI_CLI_PATH` to force a specific binary.

## opencode

```bash
npm install -g opencode-ai
opencode auth login   # pick a provider; OpenAI offers a ChatGPT Plus/Pro login
```

`--model opencode` runs `opencode run --format json` with the prompt on stdin,
and installs the declared skills under `~/.config/opencode/skills`, where
opencode discovers them. The agent opens one with its `skill` tool.

- opencode names models `provider/model`, so the flag reads
  `--model opencode:openai/gpt-6.1-terra`. `opencode models` lists what you can
  run. With no model, opencode uses your configured `model`, or picks one
  itself. Planted history requires an explicit or configured model.
- Attempts copy your logins (`auth.json`) and merge the connection settings
  from `config.json`, `opencode.json`, then `opencode.jsonc` in your config
  home. Later values override earlier ones, with nested objects merged.
  Caliper writes the merged attempt config as `opencode.json`. An API key
  opencode reads only from your shell environment doesn't reach an attempt,
  so store it with `opencode auth login`.
- A `wellknown` login loads remote configuration, including rules and MCP
  servers. Caliper supports these logins with user customizations on and no
  declared MCP servers. Other runs refuse them because remote settings can
  bypass isolation or restore ablated servers. Use a provider API key or
  OAuth login for those runs. The saved MCP inventory carries
  `mcp:(not listed)` when a `wellknown` login loads remote customizations.
- Attempts pass `--auto`, which approves anything opencode would ask
  permission for. The judge doesn't pass it, and `opencode run` rejects such
  requests without asking.
- Caliper passes a fixed `--title`, so opencode doesn't spend a call on its
  small model naming each session, and sets `OPENCODE_DISABLE_AUTOUPDATE` for
  attempts and the judge.
- Like the other judges, the `opencode` judge runs in your own environment, so
  each judge call is saved as a session in your opencode history.
- opencode's free `opencode/*` models need no login, but they're unreliable
  for evals: once their usage limit is reached, opencode retries until the
  attempt times out. Use a provider you're logged in to.
- Set `OPENCODE_CLI_PATH` to force a specific binary.

## Hermes

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes model                  # pick a default provider you have credits for, and log in
hermes auth add <provider>    # or add a provider's credentials directly
```

Hermes is a stateful, always-on agent with persistent memory, a persona, and
auto-generated skills. To keep its score comparable with the other backends,
Caliper **normalizes it to a neutral agent**:

- Every attempt runs in an isolated `HERMES_HOME` seeded with your `~/.hermes`
  (on Windows, `%LOCALAPPDATA%\hermes`) auth and config only, never `SOUL.md`
  or `MEMORY.md`.
- Attempts run with `--ignore-rules` and `--yolo`, so an approval prompt can't
  hang the non-interactive oneshot.
- Only the spec's declared skills are installed. Hermes' `--skills` flag
  *preloads*, so Caliper doesn't pass it.

`--model hermes` runs `hermes -z` (oneshot), then `hermes sessions export` to
recover the full tool-call trajectory. `--model hermes:<provider>/<model>` (for
example `hermes:anthropic/claude-opus-4-8`) selects the model; otherwise your
`~/.hermes/config.yaml` default is used. Point it at a provider you have credits
for.

If a run fails because no model is selected or a provider login lapsed, Caliper
tells you to run `hermes model`. Set `HERMES_CLI_PATH` to force a specific
binary.

### Hermes on Windows

Hermes on Windows needs Git for Windows' `sh` on `PATH`. It also only starts
from a home inside its own install root, so each attempt's isolated home is a
temporary profile, `%LOCALAPPDATA%\hermes\profiles\caliper-*`. That profile is
marked standalone, so neither its gateway nor your running one reaches across.
It shows in `hermes profile list` while the attempt runs. Caliper stops the
attempt's gateway and removes the profile once the attempt is read, and a later
run sweeps any profile left behind for a day
([ADR 0037](adr/0037-hermes-attempt-home-lives-under-the-hermes-root-on-windows.md)).

## Antigravity

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy   # sign in once with your Google account
```

`--model antigravity` runs Google's Antigravity CLI (`agy`) in print mode with
`--output-format stream-json`, and installs the declared skills under
`~/.gemini/antigravity-cli/skills`, where `agy` discovers them.
`--model antigravity:<model>` (for example
`antigravity:gemini-3.8-flash-medium`) picks the model. Run `agy models` to
list them.

- The prompt goes in on stdin as one `stream-json` message (`--input-format
  stream-json`), because `agy` refuses a flag-shaped `--print` value.
- Attempts run with `--dangerously-skip-permissions`. Print mode can't ask, and
  it skips any tool that needs approval rather than running it.
- The login stays in your OS keyring. On macOS, the attempt's isolated home
  links in `~/Library/Keychains` so `agy` finds it. An API key login
  (`GEMINI_API_KEY` with `"modelProvider": "gemini"`) or ADC (`AGY_ADC_AUTH`)
  works too: `~/.gemini/antigravity-cli/settings.json` is copied into each
  attempt, and those variables are passed through. ADC also honors
  `GOOGLE_APPLICATION_CREDENTIALS` when you select a credential file. A
  Gemini API key configuration with no `GEMINI_API_KEY` stops the run with
  setup guidance.
- Self-update is turned off during attempts and judge calls, so one run can't
  swap the binary under another.
- Set `AGY_CLI_PATH` to force a specific binary. Otherwise Caliper looks on
  `PATH`, then in `~/.local/bin/agy` (`%LOCALAPPDATA%\agy\bin\agy.exe` on
  Windows), where the installer puts it.

## CLI versions

Caliper doesn't install or update agent CLIs: update each one the way you
installed it (`claude update`, `hermes update`, or npm). Every saved run
records the `--version` of the CLI its attempts ran and of the one its judge
ran, `caliper report` shows them, and `caliper compare` flags a difference
between two runs (see [Results JSON](results.md#cli-version-fields)).

When a CLI rejects the model, rejects an option Caliper passed, or is found but
won't run, the error names the binary Caliper ran and its version. Several
installs often coexist (npm, Homebrew, an app bundle), so check it's the one
your shell runs, or set the backend's `*_CLI_PATH` to force one.

## MCP support by backend

| Backend | Local (stdio) | Remote (`http`/`sse`) |
|---|---|---|
| `claude-code` | ✅ | ✅ |
| `codex` | ✅ | ✅ header auth only |
| `hermes` | ✅ | ✅ header auth only |
| `pi` | ❌ by design | ❌ by design |
| `antigravity` | ✅ | ✅ header auth only |
| `opencode` | ✅ | ✅ header auth only |

Running a spec that declares `mcp:` on a backend that can't honor it is a hard
error, never a silent no-op.

- **`codex`** translates the `mcp:` block into `[mcp_servers.*]` tables in the
  isolated `~/.codex/config.toml`: stdio as `command`/`args`/`env`, remote as
  `url` plus a static `http_headers` map. Codex infers its single
  streamable-HTTP transport from `url`, so `http` and `sse` collapse onto it.
- **`hermes`** translates the block into its native `mcp_servers` config inside
  the isolated `HERMES_HOME`.
- **`antigravity`** writes the block as `mcpServers` in the attempt's
  `~/.gemini/config/mcp_config.json`: stdio as `command`/`args`/`env`, remote
  as `url` plus `headers`. agy speaks only Streamable HTTP to a remote, so
  `http` and `sse` collapse onto it, as on codex. agy loads MCP tools lazily
  and calls them through `call_mcp_tool`; Caliper names that call
  `mcp_<server>_<tool>`
  ([ADR 0043](adr/0043-antigravity-names-a-lazy-mcp-call-by-its-server-and-tool.md)).
- **`opencode`** translates the block into the `mcp` table of the isolated
  `opencode.json`: stdio as a `local` server whose `command` list holds the
  command and its args, with `environment`, and remote as a `remote` server
  with `url` and a static `headers` map. opencode names an MCP tool
  `<server>_<tool>`, which Caliper records as `mcp__<server>__<tool>`, the
  name `claude-code` and `codex` use.
- On all four, `${VAR}` values are resolved at the harness boundary. Your personal
  servers from the real config are kept alongside the declared set by default
  ([loaded](#loading-your-user-customizations)); an isolated run replaces them,
  so it sees only the declared set.
- **Remote OAuth** isn't supported on `codex`, `hermes`, `antigravity` or
  `opencode`: it needs an interactive browser flow the harness can't drive.
- **A stdio server that speaks only MCP 2026-07-28** works only on `claude-code`
  2.1.292 or later. On `codex`, `hermes`, `antigravity` and `opencode`, the preflight stops the run with a
  configuration error
  ([ADR 0035](adr/0035-mcp-preflight-gates-modern-only-servers-on-the-backend.md)).
- **`pi`** has no MCP by design
  ([ADR 0010](adr/0010-pi-mcp-unsupported-by-design.md)). Expose the capability
  as a CLI tool your skill drives, or as a pi extension, or run the eval on
  another backend. Running an `mcp:` spec on `pi` fails with that guidance.

See [MCP servers](spec-reference.md#mcp-servers-mcp) for the spec format.

## Planted history by backend

| Backend | `history:` |
|---|---|
| `claude-code` | ✅ |
| `codex` | ✅ |
| `pi` | ✅ |
| `opencode` | ✅ with an explicit or configured model |
| `hermes` | ❌ refused |
| `antigravity` | ❌ refused |

A spec whose tasks declare `history:` is refused before the first attempt on a
backend that can't resume from it, with a message naming the backend.

- **`claude-code`** writes the planted turns as a session file in the attempt's
  isolated `~/.claude/projects/`, under the attempt workdir, and resumes it with
  `--resume`. A planted shell call becomes a `Bash` tool call and its result,
  and a planted `/name` of a declared skill the slash command, expanded.
- **`codex`** writes them as a rollout file in the attempt's isolated
  `$CODEX_HOME/sessions/` and resumes it with `codex exec resume <id>`. A
  planted shell call becomes an `exec_command` call and its output, and a
  planted `/name` of a declared skill becomes codex's `$name` mention followed
  by the installed `SKILL.md`, as codex injects it.
- **`pi`** writes them as a session file in the attempt's isolated
  `PI_CODING_AGENT_DIR/sessions/` and resumes it with `--session <path>` in
  place of `--no-session`. A planted shell call becomes a `bash` tool call and
  its result, and a planted `/name` of an installed skill becomes what pi
  expands `/skill:name` to.
- **`opencode`** keeps sessions in a SQLite database, so Caliper writes the
  planted turns in the JSON format `opencode export` writes, imports them into
  the attempt's isolated database with `opencode import`, and resumes with
  `--session <id>`. A planted shell call becomes a finished `bash` tool call
  with its output, and a planted `/name` of a declared skill becomes what
  opencode expands that command to: the skill's body and its base directory.
  Pass `--model opencode:<provider>/<model>`, or configure `model` (or the
  active agent's `model`) in your OpenCode config. Without one, Caliper refuses
  the history before importing it; OpenCode would otherwise select the planted
  placeholder model. Import processes follow the run's cancellation, so Ctrl-C
  also stops a blocked import.
- **`hermes`** stores sessions in SQLite, and its only import path drops tool
  calls and their output, so it can't resume a history faithfully
  ([ADR 0036](adr/0036-planted-history-is-a-neutral-transcript-each-backend-resumes-natively.md)).
- **`antigravity`** stores each conversation as protobuf steps in its own
  SQLite file, with no import path, so it can't resume a history either.

See [Planted history](spec-reference.md#planted-history-history) for the spec
format.

## Loading your user customizations

By default every attempt loads your **user customizations**: user skills,
plugins, rules, settings, MCP servers and account connectors, alongside the
spec's declared skills and servers
([ADR 0028](adr/0028-runs-load-user-customizations-by-default.md)), so a score
measures the skill in the agent you actually use, including skills that rely on a
hosted OAuth connector a spec can't declare. `--no-user-customizations`, or
`user_customizations: false` in the spec, isolates a run to the declared skills and servers; see
[Portable scores](../README.md#portable-scores) for when that's needed.

| Backend | What is loaded |
|---|---|
| `claude-code` | The `mcpServers` in your `~/.claude.json`, plus claude.ai connectors and the skills and plugins synced from claude.ai, `~/.claude/skills`, `CLAUDE.md`, `settings.json` (including hooks, permissions and env), and enabled user-scope plugins from the installed registry |
| `codex` | The `[mcp_servers.*]` tables in your `~/.codex/config.toml`, your installed plugins (`~/.codex/plugins`, copied into the attempt), ChatGPT apps (`codex_apps`), `~/.codex/skills`, `AGENTS.md` / `AGENTS.override.md`, and `config.toml` settings (the top-level model pin is still stripped) |
| `hermes` | `~/.hermes/skills` and `config.yaml` (`%LOCALAPPDATA%\hermes` on Windows), including `mcp_servers`; persona and memory remain excluded |
| `pi` | Nothing: no MCP by design. The run records it as off, and warns only if a flag or the spec asked for it |
| `antigravity` | The `mcpServers` in your `~/.gemini/config/mcp_config.json`, the immediate child skill folders in `~/.gemini/antigravity-cli/skills`, `~/.gemini/config/skills` and `~/.gemini/skills`, the rules files `AGENTS.md` / `GEMINI.md` in `~/.gemini` and `~/.gemini/config`, and `antigravity-cli/settings.json`. Nested skill folders are skipped, matching agy's discovery. Plugins and `config/rules/*.md` are not loaded yet |
| `opencode` | The merged `config.json`, `opencode.json` and `opencode.jsonc` settings in `~/.config/opencode`, including `mcp` and plugins, `~/.config/opencode/skills` and `~/.config/opencode/skill`, `AGENTS.md`, and your `agent(s)`, `command(s)`, `mode(s)`, `plugin(s)` and `tool(s)` directories (copied into the attempt, preserving symlinks). A `wellknown` login can also load remote customizations; its MCP inventory is marked incomplete |

- **The spec wins a name clash.** A declared skill or server replaces your own of the
  same name, including when the declared name is ablated, in the attempt's copy of the config. Your real config is never
  changed.
- **The judge's isolation from account connectors and synced skills is unchanged**, whatever the setting.
- **The run says so**: a notice at the start (attempts can act on those accounts
  without asking), and the saved run records what was loaded, marking MCP as
  `mcp:(not listed)` when a source such as codex plugins can't be listed. See
  [Results JSON](results.md#results-json) for how `compare` uses it.
- **`--ablate` names only declared skills and servers.** It only names what the spec
  declares. A spec can pin the setting; see
  [the spec reference](spec-reference.md#user-customizations-user_customizations).

User skills and Claude plugin installations are copied, not linked back to the
original. Plugin registry paths point at the private copies. An enabled Claude plugin
whose installation directory is missing is skipped with a warning; invalid
plugin metadata produces a configuration error naming the affected file.
Codex plugin trees preserve symlinks, including dangling links and loops. Skills stay
available through discovery, and unexpected user-skill activations count against
`activates:`. Claude plugin skills keep their `plugin:skill` identity. Skills
the CLI ships itself are skipped: hidden folders such as codex's `.system`, and
the skills in hermes' `.bundled_manifest`. Of two user skills with the same
name, the first in sorted path order is used.

Hermes continues to pass `--ignore-rules` and copies no `SOUL.md` or memory files
(ADR 0005). Each attempt starts independently; its writes stay in its temporary
home. Hooks in settings/plugins run normally under the attempt timeout. They
are not separately preflighted. Caliper's noninteractive CLI flags still take
precedence over interactive permission settings.

Isolation retains credentials and connection configuration. The top-level
allowlists are deliberate; unknown keys are removed rather than silently
carrying new behavioral settings into an isolated run (ADR 0028):

| Backend | Keys retained with `--no-user-customizations` |
| --- | --- |
| Codex | `model_provider`, `model_providers`, `cli_auth_credentials_store`, `forced_login_method`, `forced_chatgpt_workspace_id`, `chatgpt_base_url`, `openai_base_url` |
| Hermes | `model`, `provider`, `providers`, `custom_providers`, `terminal` |
| opencode | `provider`, `enabled_providers`, `disabled_providers`, `model`, `small_model` |

Nested values in these sections are retained in full. Codex provider definitions
therefore keep their endpoints and authentication options; Hermes keeps
`model.base_url` and terminal backend/connection options. Hermes also retains
its credential `.env` and auth files. Preserving `terminal` means isolation does
not silently switch a Docker or SSH setup to local execution. Terminal options
can affect execution behavior; they are an explicit exception to the removal of
behavioral settings such as `agent`, `skills`, and memory configuration.

These lists were checked against the upstream
[Codex configuration reference](https://developers.openai.com/codex/config-reference/),
[Hermes model configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuring-models),
and [Hermes terminal configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration#terminal-backend-configuration).
Codex's top-level `model` exception from ADR 0012 remains in both modes.
Absolute paths in user settings or hooks are not rewritten; this is measurement
isolation, not a security boundary (ADR 0027).

The switch applies to attempts. The judge's bare-prompt path retains its existing
connector controls and uses the caller's CLI configuration; this switch does not
strip the judge's global skills, rules or settings.
