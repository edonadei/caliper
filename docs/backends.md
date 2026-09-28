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
- When `--judge-model` names no model, the judge uses its CLI's own default
  model, like the skill does.

## Claude Code

Install and authenticate the `claude` CLI. `--model claude-code` uses your
existing Claude Code auth, with no extra configuration. On macOS the Keychain
entry wins over `~/.claude/.credentials.json`, as it does for the CLI itself, so
a stale file left next to a valid Keychain login doesn't break attempts.

An expired OAuth session or invalid API key stops the run as a configuration
error. Run `claude`, then `/login`, and retry the eval. Caliper reads these
failures from the CLI's error output, not from an agent discussing authentication.

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
- It reuses your `~/.pi/agent` `auth.json`, `settings.json` and `models.json`,
  so custom providers from `models.json` work in attempts. `!command` values in
  `models.json` still run at request time, as they do when you run pi yourself,
  but inside the attempt: `HOME` is the attempt's own empty home and only a few
  variables (`PATH`, `LANG`, …) are passed through. So an `apiKey` of
  `!cat ~/.config/x/token` or `$MY_TOKEN` resolves to nothing there. Use a
  literal key or an absolute path in the command.
  The `:model` half of `--model pi:<model>` overrides pi's configured default.
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
| `claude-code` | The `mcpServers` in your `~/.claude.json`, plus claude.ai connectors, `~/.claude/skills`, `CLAUDE.md`, `settings.json` (including hooks, permissions and env), and enabled user-scope plugins from the installed registry |
| `codex` | The `[mcp_servers.*]` tables in your `~/.codex/config.toml`, your installed plugins (`~/.codex/plugins`, copied into the attempt), ChatGPT apps (`codex_apps`), `~/.codex/skills`, `AGENTS.md` / `AGENTS.override.md`, and `config.toml` settings (the top-level model pin is still stripped) |
| `hermes` | `~/.hermes/skills` and `config.yaml`, including `mcp_servers`; persona and memory remain excluded |
| `pi` | Nothing: no MCP by design. The run records it as off, and warns only if a flag or the spec asked for it |

- **The spec wins a name clash.** A declared skill or server replaces your own of the
  same name, including when the declared name is ablated, in the attempt's copy of the config. Your real config is never
  changed.
- **The judge's connector isolation is unchanged**, whatever the setting.
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
