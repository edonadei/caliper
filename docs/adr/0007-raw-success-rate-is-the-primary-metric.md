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
- Raw rate and pass@k are monotonic at equal *usable* counts, so the regression
  sign is unchanged in the common case — but they can disagree when the two sides
  have different unusable counts, which is another reason the raw score (not a
  k-dependent transform) is the canonical basis for the delta and regression.
- Old results JSON still loads (the removed `avg_pass_at_k` field is ignored; the
  score recomputes from `successes`/`usable`).
