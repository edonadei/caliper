# A CLI refusal is read from what the CLI wrote

A backend's CLI tells caliper it refused an invocation in three ways: the
provider is busy (a throttle), the account is out of budget (a spending cap), or
the CLI is misconfigured (a lapsed login, a model it cannot run). Each backend
used to detect a misconfiguration in its own `_diagnose`, by looking for words
in text that included the **agent's answer**: claude-code read the final output
whatever the exit code, and codex, hermes and pi read the whole stdout stream
after a nonzero exit. An agent that wrote "not logged in" or "authentication"
about its task aborted the whole run. Throttles and caps had the same problem,
patched separately with `answered()` in two callers
([0019](0019-an-attempt-may-be-invoked-more-than-once.md)).

A refusal is now read **once**, by the harness, and **only from what the CLI
wrote**: stderr (on a failed run, or when the agent never spoke), plain-text
stdout when nothing parsed as the agent's stream (never a JSON event: a stream
echoes the prompt), and the error events a CLI writes into its own stream (claude's `result` flagged `is_error`, codex's `error` and
`turn.failed`, pi's `stopReason: error` messages). The agent's turns are never
read.

## One order

The classifier checks a spending cap, then a throttle, then the backend's
misconfigurations. A cap whose message mentions the subscription is a cap, not a
bad login; an exhausted quota stops the run with the cap's own message rather
than a credentials diagnosis (hermes used to list a bare "quota" among its
credential markers).

## Declared, not performed

A backend declares its misconfigurations as `config_signals`: markers and the
diagnosis to show. Markers every CLI shares (401, "not logged in", …) are one
list. A check that needs structure rather than words (an envelope's status code,
a crash's stack trace, a condition on the exit code) stays a small `_diagnose`
hook, run over the same text after the cap and throttle checks. This continues
[0020](0020-a-backend-declares-its-chores-rather-than-performing-them.md).

## What the rest of the run sees

A misconfiguration still raises `HarnessConfigurationError` and stops the run.
A throttle or cap travels on `AttemptResult.refusal`, and the retry seam and the
pre-judge outcome act on that field instead of matching text themselves.

## Consequences

- A CLI error event after a real conversation now counts. A pi or claude run
  that talked, then hit a 429 its CLI reported, is retried; before, the answer
  it had started masked the throttle.
- stderr after a successful run with an answer is ignored, so a CLI that warns
  there during a good run refuses nothing. hermes prints its reply on stderr
  whatever the exit code, so its stderr is ignored on any exit once the agent
  gave a real answer.
