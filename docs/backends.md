# Backends

Caliper runs every skill through a real CLI agent, so every backend can load and
run a skill the way a user would. This page covers setup for each one, how
`--model` and `--judge-model` pick an engine, and which backends support `mcp:`.

For the short version, see [Choosing an engine](../README.md#choosing-an-engine)
in the README.

## Selecting an engine

The engine is not stored in the spec. `--model` picks the engine that runs the
skill, `--judge-model` picks the one that grades it, and both default to
`claude-code`. Each flag accepts a `backend:model` pair, a bare backend name, or
a bare model name:

```bash
# Backend and model together
caliper run my-skill.eval.yaml --model codex:gpt-5.6-sol

# Backend only (that backend's default model)
caliper run my-skill.eval.yaml --model codex

# Model only (backend stays claude-code)
caliper run my-skill.eval.yaml --model claude-fable-5

# Select the judge engine independently
caliper run my-skill.eval.yaml --model codex --judge-model claude-code:claude-haiku-4-5-20251001
```

Accepted backends: `claude-code`, `codex`, `pi`, `hermes` (alias: `claude` →
`claude-code`).

The skill engine and judge engine are independent: you can test a Codex skill
with a Claude judge, or any other pairing.

There is no direct-API backend. To run against API-priced billing, configure one
of these CLIs with an API key (for example `ANTHROPIC_API_KEY` or
`OPENAI_API_KEY`).

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
- The `judge_model` likewise comes from the `claude-code` judge's JSON output
  when you don't name one.
- `judge_model` stays empty for an `assert:`-only run, where no LLM judge ran.
- When `--judge-model` is omitted, the `claude-code` judge pins `claude-sonnet-5`
  at execution time so it doesn't inherit a stale model from the installed
  Claude CLI. That pin is not written into `RunMeta` unless you pass it
  explicitly or the autorater reports what it used.

## Claude Code

Install and authenticate the `claude` CLI. `--model claude-code` uses your
existing Claude Code auth, with no extra configuration.

## Codex

```bash
npm install -g @openai/codex
codex login
```

`--model codex` calls `codex exec`. If the Codex desktop app is installed,
Caliper prefers the app-bundled binary over `codex` on `PATH`. Set
`CODEX_CLI_PATH` to force a specific binary.

## pi

```bash
npm install -g @earendil-works/pi-coding-agent
pi   # then authenticate (e.g. /login for a subscription provider, or set the provider API key)
```

`--model pi` runs `pi --print --mode json` and installs the declared skills under
pi's agent dir, where pi discovers them.

- Caliper never passes pi's `--skill` flag, because it *preloads* the skill.
  Discovery is pi's default behavior, which is why pi also has `--no-skills`.
- It reuses your `~/.pi/agent` auth and settings. The `:model` half of
  `--model pi:<model>` overrides pi's configured default.
- pi's built-in default provider is `google`, so `--model pi` with no model
  relies on your pi config to resolve a provider you're authenticated for.
- Set `PI_CLI_PATH` to force a specific binary.

## Hermes

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes login   # authenticate
hermes model   # pick a default model/provider you have credits for
```

Hermes is a stateful, always-on agent with persistent memory, a persona, and
auto-generated skills. To keep its score comparable with the other backends,
Caliper **normalizes it to a neutral agent**:

- Every attempt runs in an isolated `HERMES_HOME` seeded with your `~/.hermes`
  auth and config only, never `SOUL.md` or `MEMORY.md`.
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
binary. Hermes updates itself (`hermes update`), so it isn't part of
`caliper update-cli`.

## Checking CLI versions

```bash
caliper update-cli --check
```

## MCP support by backend

| Backend | Local (stdio) | Remote (`http`/`sse`) |
|---|---|---|
| `claude-code` | ✅ | ✅ |
| `codex` | ✅ | ✅ header auth only |
| `hermes` | ✅ | ✅ header auth only |
| `pi` | ❌ by design | ❌ by design |

Running a spec that declares `mcp:` on a backend that can't honor it is a hard
error, never a silent no-op.

- **`codex`** translates the `mcp:` block into `[mcp_servers.*]` tables in the
  isolated `~/.codex/config.toml`: stdio as `command`/`args`/`env`, remote as
  `url` plus a static `http_headers` map. Codex infers its single
  streamable-HTTP transport from `url`, so `http` and `sse` collapse onto it.
- **`hermes`** translates the block into its native `mcp_servers` config inside
  the isolated `HERMES_HOME`.
- On both, `${VAR}` values are resolved at the harness boundary. Your personal
  servers from the real config are kept alongside the declared set by default
  ([loaded](#loading-your-user-customizations)); an isolated run replaces them,
  so it sees only the declared set.
- **Remote OAuth** isn't supported on `codex` or `hermes`: it needs an
  interactive browser flow the harness can't drive.
- **`pi`** has no MCP by design
  ([ADR 0010](adr/0010-pi-mcp-unsupported-by-design.md)). Expose the capability
  as a CLI tool your skill drives, or as a pi extension, or run the eval on
  another backend. Running an `mcp:` spec on `pi` fails with that guidance.

See [MCP servers](spec-reference.md#mcp-servers-mcp) for the spec format.

## Loading your user customizations

By default every attempt loads your **user customizations**: the MCP servers and
account connectors your CLI loads by itself (user skills, plugins, rules and
settings are planned to join them: #177), merged with the spec's `mcp:` block
([ADR 0028](adr/0028-runs-load-user-customizations-by-default.md)), so a score
measures the skill in the agent you actually use, including skills that rely on a
hosted OAuth connector a spec can't declare. `--no-user-customizations`, or
`user_customizations: false` in the spec, isolates a run to the declared servers; see
[Portable scores](../README.md#portable-scores) for when that's needed.

| Backend | What is loaded |
|---|---|
| `claude-code` | The `mcpServers` in your `~/.claude.json`, plus your claude.ai connectors (`--strict-mcp-config` is dropped) |
| `codex` | The `[mcp_servers.*]` tables in your `~/.codex/config.toml`, plus ChatGPT apps and plugins (surfacing as `codex_apps`) |
| `hermes` | The `mcp_servers` in your `~/.hermes/config.yaml` (and `inherit_mcp_toolsets`) |
| `pi` | Nothing: no MCP by design. The run records it as off, and warns only if a flag or the spec asked for it |

- **The spec wins a name clash.** A declared server replaces your server of the
  same name, in the attempt's copy of the config. Your real config is never
  changed.
- **The judge stays isolated**, whatever the flag says.
- **The run says so.** Under the default it prints one line at the start; when
  a flag or the spec asked for it, a full warning naming the source: the score
  depends on this machine's setup, and attempts can act on those accounts
  without asking. The saved run records `user_customizations` and the loaded server names (or
  "unknown" when a source such as codex plugins or hermes' inherited toolsets
  can't be listed), the report header lists them, and `caliper compare` warns
  when two runs loaded different customizations, or compares two backends with
  user customizations loaded
  MCP (see [Results JSON](results.md#results-json)).
- **`--ablate` can't remove one of your servers.** It only names what the spec
  declares.
- **A spec can pin it** with `user_customizations: false` (portable) or `true` (needs
  your setup); the flags override it for one run. See
  [the spec reference](spec-reference.md#user-customizations-user_customizations).
