# Raw success rate is the primary metric, not pass@k

## Context

Caliper originally headlined **pass@k** — `1 − (1 − successes/usable)^usable`,
the probability that at least one of k attempts passes. That is the HumanEval /
code-generation metric, where you sample k candidates and keep the best one.

But an agent skill runs **once** in production — there is usually no oracle to
pick the good run out of k. Reporting pass@k there measures a scenario that
doesn't happen and **flatters flaky skills**: a skill that passes 1 of 3 attempts
shows `70.4%`, and `2/3` shows `96.3%`. A reader reasonably reads "70%" as "works
most of the time," which is false.

## Decision

The **primary metric is the raw per-attempt success rate**, `successes / usable`
— "how often does a single run work." It is `TaskResult.score` and the aggregate
`avg_score`, and it is what every table headline, the compare `Δ`, and the
`regression` verdict are computed on.

**pass@k is kept as a secondary view, alongside pass^k** (`p^k` = P(all k pass),
the strict consistency view). Both are on every task in the JSON (`pass_at_k` /
`pass_hat_k`) and shown under `--verbose`. Nothing is lost; the retry-optimistic
and all-pass lenses are one flag away.

## Consequences

- The headline number now means "single-run reliability" and is **k-invariant**,
  so it is directly comparable across runs with different k (pass@k and pass^k
  both drift with k).
- `AggregateScore.avg_pass_at_k` → `avg_score`; `TaskResult` gains a computed
  `score` (primary) and `pass_hat_k`, keeping `pass_at_k` as a stored secondary.
  *(Amended: `pass_at_k` is computed too — see the amendment below.)*
- Raw rate and pass@k are monotonic at equal *usable* counts, so the regression
  sign is unchanged in the common case — but they can disagree when the two sides
  have different unusable counts, which is another reason the raw score (not a
  k-dependent transform) is the canonical basis for the delta and regression.
- Old results JSON still loads (the removed `avg_pass_at_k` field is ignored; the
  score recomputes). *(Amended: it recomputes from the attempts, not from a
  stored `successes` — see below.)*

## Amendment: every number a task reports is derived from its attempts

`TaskResult` kept `successes`, `unusable` and `pass_at_k` as **stored** fields
while `usable`, `score` and `pass_hat_k` were computed — an asymmetry with no
reason behind it. The runner filled the stored three from `score_outcomes` while
the model derived the other three from `self.attempts`, so `usable`, `score` and
`pass_hat_k` were each computed twice from two different inputs, and a
hand-edited or older file could carry a `pass_at_k` that disagreed with the
`score` printed beside it.

All six are now derived from the attempts, and `score_outcomes` is deleted. The
rule this ADR is about is unchanged — every denominator is still usable attempts
— but there is one derivation of it instead of two, and no stored count can
contradict the attempt list it sits next to. The metric formulas moved to
`caliper/schema/results.py`, beside the `Outcome.is_usable` that carves out the
denominator; `caliper/scoring.py` keeps the roll-ups across tasks.

The numbers are still **serialized**, so a saved run reads exactly as before; the
stored values are simply ignored on load.
