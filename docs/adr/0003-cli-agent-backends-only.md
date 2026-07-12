# Caliper supports only CLI-agent backends; direct API is not a backend

We remove the `claude-api` and `openai-api` backends entirely — from both the
skill-under-test harness role and the judge role — leaving `claude-code`,
`codex`, and `pi`. Direct API access (e.g. for API-priced billing) is achieved
by configuring one of those CLI harnesses to use an API key, not by selecting a
separate backend. We chose this because an API backend makes a single tool-less
message call: it cannot load a skill (it only injects `SKILL.md` text, so
progressive disclosure is unmeasurable), which means its pass@k is not
apples-to-apples with a CLI backend that actually runs the skill. Keeping it
invited users to "test a skill" through an engine that structurally can't use
one.

## Considered options

- **Split the surface: API valid as judge, invalid as harness.** Rejected. It
  keeps the cheap single-call grader but leaves two enums, two validation
  rules, and a surface where `claude-api` means "fine here, forbidden there" —
  complexity for a role the CLI backends already cover.
- **Keep both roles (status quo).** Rejected: the harness role is structurally
  misleading (see above), and the whole point of caliper is that a pass@k
  number means the same thing across backends.

## Consequences

- **No cheap single-call LLM judge.** The API judges were the only tool-less
  graders; every `expect:` (LLM) judgment now spins up a full CLI agent, or a
  spec uses a deterministic Python `assert:`. The default judge is already
  `claude-code`, so defaults are unaffected.
- **Breaking change for specs.** Any spec with `skill.backend` or
  `judge.backend` of `claude-api`/`openai-api` (or the `anthropic`/`openai`
  aliases) now fails validation. The `claude` alias for `claude-code` stays.
- **Re-adding is real work.** Restoring an API backend means re-implementing the
  harness, judge, schema enum, and CLI plumbing — the reversibility price we
  accept for a coherent, every-backend-can-load-a-skill surface.
