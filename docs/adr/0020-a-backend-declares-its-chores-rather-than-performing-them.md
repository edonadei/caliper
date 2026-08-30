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
| `env_passthrough` (+ `_isolated_env`) | any extra vars, any extra `PATH` prefixes | isolated `HOME`, deduplicated `PATH`, allowlisted passthrough |
| `_parse_stream` | this agent's turns | supplies the last-assistant tail |

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
must see the verbatim copy already in place, and claude-code's Keychain fallback
must be able to test whether `.credentials.json` was seeded before deciding to
shell out for it. Reversing the two would silently break both, so the order is
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
