# A backend declares its chores rather than performing them

Four CLI backends each hand-rolled the same three chores: seeding the isolated
home with the user's real config, locating the CLI binary, and building the
attempt's environment. A fourth thing — recovering the final answer from the
last assistant turn when the stream never named one — was copied verbatim into
all four stream parsers.

`CliHarness` was already a template method owning the expensive parts (spawn,
timeout, cancellation, usage safety). It simply stopped one hook short each
time, so `_prepare` and `_environment` were "do it yourself" where
[0009](0009-mcp-secrets-interpolated-at-the-harness-boundary.md)'s
`resolve_servers` had already shown the better shape: the base does the work,
the backend contributes the one fact that differs.

## Data, not code

The chores are now declared:

| Hook | The backend says | The base does |
|---|---|---|
| `seed_files(ctx)` | which `(real, isolated)` files to copy | copies each one that exists, creating parents |
| `cli_name` / `cli_path_env_var` / `cli_candidates()` | what the binary is called and where else to look | env-var override → candidates → `PATH` |
| `cli_unavailable_message` / `cli_version_timeout` | what to say when the CLI is missing, and how long `--version` may take | finds the CLI, probes it, raises the message (amended below) |
| `env_passthrough` (+ `_isolated_env`) | any extra vars, any extra `PATH` prefixes | isolated `HOME`, deduplicated `PATH`, allowlisted passthrough |
| `_read` | what its finished process said, read once into an `AgentReport` | supplies the last-assistant tail, salvages raw stdout, classifies refusals; `AgentReport` guards usage and the MCP inventory |

The reason the chores stayed duplicated for so long is that each backend has an
*exception* — and an exception looks like a reason to keep your own copy. Every
one of them turned out to be expressible as data:

- codex omits `config.toml` from `seed_files` because it **rewrites** that file
  rather than copying it (stripping `model =` and the user's ambient
  `[mcp_servers*]`); the copy-verbatim policy of
  [0012](0012-cli-harnesses-copy-cli-config-verbatim.md) is unchanged for
  everything it does seed.
- hermes omits `SOUL.md` and `MEMORY.md` from `seed_files`; that omission *is*
  the neutralization of a stateful agent
  ([0005](0005-hermes-backend-normalized-to-neutral-agent.md)), and it now reads
  as a list rather than as a loop you have to notice.
- claude-code contributes `PATH` prefixes (nvm's Node, Homebrew) and forwards
  API keys only when no file credentials were seeded.

## Ordering is part of the contract

`_seed_home` runs **before** `_prepare`. A backend that has to rewrite a config
must see the verbatim copy already in place, and on macOS claude-code's Keychain
credentials must replace a seeded `.credentials.json`, which may be stale (#180),
rather than be overwritten by it. Reversing the two would silently break both, so the order is
stated in `run` and in `_prepare`'s docstring rather than left to be discovered.

## Costs accepted

**claude-code's environment changed.** It previously forwarded `TMPDIR` alone,
and only on macOS; it now forwards `LANG`, `LC_ALL`, `TERM`, `TMPDIR` like every
other backend. That is a real behaviour change made deliberately: a backend
differing from its siblings for no recorded reason is the thing this record is
against, and locale/terminal shape is not state an agent carries between
attempts. It is also the reason to look here first if claude-code's stream ever
parses differently — though its `--output-format stream-json` should be
indifferent to `TERM`.

**`PATH` is deduplicated now.** The old spelling was `extra_path + PATH`
verbatim, so an entry already on `PATH` appeared twice and the earlier
occurrence was not necessarily the staged one. The base removes prefix entries
from the tail, which is what makes `extra_path` actually take precedence.

**`_ensure_ready`'s message was left alone.** Templating it was considered and
rejected: the three messages share a shape but not a subject — codex's talks
about API billing and names no env var, while pi's and hermes' name an install
command and a `*_CLI_PATH`. A template would flatten user-facing prose that
exists to be read at the moment a run fails, which is the wrong thing to
economize. Two near-matches out of three is not a shared implementation.

**Amended: the readiness check is performed too.** Each backend used to
write the same `_ensure_ready` / `_cli_available` pair, differing only in the
message and the `--version` timeout. A backend now declares
`cli_unavailable_message` and `cli_version_timeout`, and the base does the
probe. The message is still written out whole by each backend, as decided
above; only the probe is shared.

**Amended: one reader per backend.** Reading the finished process used to be
about ten hooks (`_parse_stream`, `_usage`, `_resolved_model`, `_cli_text`,
`_exposed_skills`, `_loaded_user_customizations`, `_fallback`, …), each handed
raw stdout and each parsing it again; claude-code walked its stream five times
per attempt. A backend now implements one `_read(proc, ctx) -> AgentReport`
in a single pass, and `run` decides what each fact means. Usage and the MCP
inventory travel as unread `read_*` callables that `AgentReport` calls behind
the safe-usage guard, so a reader cannot skip it, and an isolated attempt never
reads its MCP inventory. What stayed a hook
is policy that genuinely differs: `_diagnose`, `_error_field`, hermes'
`_cli_text` (its reply shares stderr with its errors), and
`_recover_timed_out`, which spawns a process rather than reading one.
`salvages_raw_stdout` is declared as data, like the chores above.

**Amended: the user layer is staged beside the base.** Staging the user
customizations an attempt loads (docs/adr/0028) moved out of `CliHarness` into
`caliper/harness/user_layer.py`, which does the work on the base's behalf. The
rule is unchanged: a backend still only declares where it keeps that layer
(`user_rules`, `user_settings_file`, `user_skill_name`, `bundled_skill_names`,
`stage_plugins`), and those hooks are public because a sibling module calls them.
