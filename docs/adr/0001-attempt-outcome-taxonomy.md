# Attempt outcome taxonomy over a bare `passed` bool

> **Amended by [0014](0014-activation-is-a-check-type-not-a-separate-command.md):**
> a seventh value, `not_checked`, was added when `activates:` made it possible to
> author a task with **no execution check at all**. Such an attempt reached no
> judge, but nothing went wrong — so it is neither usable nor noise. That split
> the single usable/unusable question in two: `is_usable` (does it count toward
> the score?) and `is_execution_noise` (should it be *reported* as a problem?).
> Before the amendment those were the same predicate, and a correct
> `activates:`-only spec reported itself as a `judge_error`.
>
> **Amended:** the single seam is now `assemble_attempt` in
> `caliper/attempt.py`. It walks the same precedence, but the early exits that
> skip the paid judge *are* the labels, so a separate `classify_outcome`
> re-deriving them was the same rule written twice. `classify_pre_judge` and
> `judge_outcome` are the two halves it composes.
>
> **Amended (#132):** `infra_error` also covers a zero-exit attempt where **no
> model call was observed**: nothing parsed from the agent's stream and no
> tokens reported. Unlike a throttle it is not transient, but it is not a
> verdict on the skill either: judging it scored an expired pi login as a real
> 0%, and an `activates: []` probe passed because nothing fired. Where a backend
> can *recognise* the cause (pi's OAuth refresh failure in its stream), it still
> raises `HarnessConfigurationError` and aborts the run as below; this label is
> the backstop for the causes no backend recognises. `classify_pre_judge` now
> returns the evidence with the label, so the record's `assert_evidence` names
> the branch that fired rather than re-deriving it.
>
> **Amended by [0029](0029-every-step-runs-under-the-same-rules.md):** an
> `assert:` or script check that runs past its time limit yields **no verdict**
> rather than a `task_fail`, and is dropped under rule B like an errored
> autorater. A `setup:` that runs past its limit is an `infra_error`, like one
> that exits nonzero.
>
> **Amended:** `assemble_attempt` also takes the two endings the runner used to
> label itself: a failed `setup:` hook (`SetupFailed`, an `infra_error` before
> the agent runs) and a spawn the run's cancellation killed (no record at all).
> Precedence is now `setup failed → cancelled → timeout → infra_error → cheat →
> not_checked → judge`. `caliper/outcome.py` is folded into `caliper/attempt.py`;
> `classify_pre_judge` and `judge_outcome` are private helpers there.

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
  *transient, mid-run* throttles are `infra_error` (and, since the #132
  amendment above, an attempt where no model call was observed).
- **An unavailable model** (the provider's 404: unknown, retired, or not on this
  account) is treated the same way, for the agent and the judge alike (issue
  #139). It is not transient — every remaining attempt would meet it — so
  recording it as `infra_error` or `judge_error` per attempt would pay for each
  agent run only to discard it. The attempt that hit it is dropped, including
  any surviving `assert:` verdict, because the run is stopping anyway. A judge
  auth failure or rate limit stays a per-attempt `judge_error`.
