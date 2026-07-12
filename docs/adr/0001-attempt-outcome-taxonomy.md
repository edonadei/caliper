# Attempt outcome taxonomy over a bare `passed` bool

An attempt's result is a typed `Outcome` (`pass`, `task_fail`, `judge_error`,
`infra_error`, `timeout`, `cheat`), not just `passed: bool`, so infrastructure
and judge noise stop being scored as task failure. The outcome is classified
**once**, in a pure `classify_outcome(harness, cheat_violations, judge)`
function — the single seam — with precedence `timeout → infra_error → cheat →
judge_error → judge verdict`. Unusable outcomes (`judge_error`/`infra_error`/
`timeout`) are excluded from the pass@k denominator and surfaced as a separate
"unusable attempts" count. See [CONTEXT.md](../../CONTEXT.md) for the term
definitions.

## Considered options

- **String-sniffing at the seam** (e.g. `error == "timeout"`, or grepping the
  autorater reason for "unparseable"). Rejected: the taxonomy would depend on
  brittle conventions any backend could set differently. Instead each signal is
  carried *structurally* — `AttemptResult.timed_out`, `JudgeResult.errored` —
  and detection of transient throttles lives in one shared, unit-testable
  `looks_like_infra_failure(text)` helper rather than per-backend flags (favours
  cross-backend consistency).

## Consequences

- **Dual-check tasks (`assert:` + `expect:`) and an errored autorater.** The
  errored autorater is *dropped* rather than treated as a fail: the surviving
  `assert:` verdict stands, and `judge_error` fires only when *no* verdict
  survives (an `expect:`-only task whose autorater flaked). Trade-off: a task
  with a weak `assert:` and a strong `expect:` will score `pass` when the judge
  flakes — the inverse of the inflation this ADR fixes. Accepted because the
  recommended pattern is a single check per task.
- **Enforcing single-check per task** (one of `assert:` / `expect:`, never both)
  is deliberately *not* done here — it is a breaking spec change touching many
  shipped evals and all doc locations, and belongs in its own issue/ADR.
- **Startup auth/login misconfiguration** keeps its existing behaviour: it raises
  `HarnessConfigurationError` and aborts the whole run (fail-fast on a broken
  machine), rather than being classified as `infra_error` per attempt. Only
  *transient, mid-run* throttles are `infra_error`.
