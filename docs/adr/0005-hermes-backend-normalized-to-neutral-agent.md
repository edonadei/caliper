# Hermes is supported as a stateful backend normalized to a neutral agent

We admit `hermes` (Nous Research's Hermes Agent CLI) as a flat backend, but only
after **stripping it to a neutral agent** on every invocation: each attempt runs
in an isolated `HERMES_HOME`, with `--ignore-rules` set (skips `AGENTS.md`,
`SOUL.md`, memory, and preloaded-skill injection) and no `SOUL.md`/`MEMORY.md`
copied into that home — so the only skill in play is the `--skills`
skill-under-test. Full trajectory (for `expect:` autoraters that inspect tool
calls) is recovered per attempt by running `hermes -z <prompt>` and then
`hermes sessions export --source cli -`, whose JSONL is a standard
OpenAI-style transcript (`assistant`/`tool`/`user` turns with `tool_calls` +
`tool_call_id`) parsed into `ConversationTurn`s. The judge half uses plain
`hermes -z` (final verdict text only; no export needed). Model/provider selection
reuses the existing `hermes:<provider>/<model>` addressing, passed straight to
Hermes's `-m`.

We chose to strip rather than run Hermes "as a real user does" because a pass@k
number is only comparable across backends when the k attempts are independent
and measure *the skill in isolation*. Hermes's always-on `MEMORY.md`, `SOUL.md`
persona, and auto-generated skills would otherwise make attempt N depend on
attempt N-1 and let Hermes's own skill-authoring stand in for the
skill-under-test — inflating pass@k and destroying apples-to-apples comparison
with `claude-code`/`codex`/`pi`. This follows the project's standing preference
for cross-backend consistency over per-backend realism.

## Considered options

- **Preserve Hermes's persona + memory (realistic Hermes).** Rejected as the
  default: it measures Hermes-with-its-memory, not the skill, and yields an
  openclaw-style "not apples-to-apples" caveat. A selectable realistic variant is
  a possible later refinement, not the initial rule.
- **Final-output-only transcript (skip `sessions export`).** Rejected: cheaper,
  but any `expect:` that inspects tool calls becomes unjudgeable, breaking parity
  with the other CLI backends whose streams carry tool turns. The export path
  turned out clean enough that the fidelity gap wasn't worth accepting.
- **Drive Hermes's ACP server for a live structured stream.** Rejected: a
  persistent server is far heavier than caliper's spawn-once-per-attempt model,
  for output the two-step run+export already provides.

## Consequences

- **Two subprocesses per harness attempt** (`-z` run, then `sessions export`),
  versus one for the other backends — the price of full-fidelity trajectory from
  a CLI whose non-interactive mode prints only final text.
- **Auth seeding is verified, not a blocker.** Copying `auth.json` /
  `config.yaml` / `.env` into an isolated `HERMES_HOME` authenticates and runs
  end-to-end. The one requirement is that `config.yaml`'s default provider/model
  points at a provider the user actually has credits for — the same precondition
  pi has (its issue #10). An earlier failure (`Codex auth is missing
  access_token`) was a stale default provider, not an unreachable token; a
  `_diagnose` hook like pi's should surface exactly this case.
- **`-z` persistence is verified.** Oneshot persists exactly one session in the
  isolated store, and `sessions export --source cli -` returns it as OpenAI-style
  JSONL with `terminal` tool calls *and their outputs* captured. Fallback to
  `hermes chat -q -Q` is available but unnecessary.
- **Normalization is load-bearing.** If a future change copies `SOUL.md`/memory
  in, or drops `--ignore-rules`, Hermes silently stops being apples-to-apples;
  the neutral-agent invariant must be asserted in tests.
