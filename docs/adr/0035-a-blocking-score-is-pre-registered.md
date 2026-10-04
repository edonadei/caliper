# A blocking score is pre-registered

`caliper run` reported a score and exited `0` however low it was, so CI could
show a regression but never stop one. Exit `3` was reserved for "ran cleanly,
missed the bar", but nothing declared a bar.

A score can only block if the bar was fixed **before** the run produced it.
Otherwise the bar gets tuned to whatever the run returned. So the bar lives in
the spec, as `bar:`, committed beside the tasks it is measured on. A pull
request that changes a skill is judged by the bar already on the base branch,
and a PR that moves the bar is visible in its diff (the GitHub Action also says
so in its comment).

A barred rate is checked with its **95% Wilson interval** over usable attempts
pooled across tasks, not with the point estimate. A run clears the bar when the
whole interval is at or above it, misses it (exit `3`) when the whole interval
is below, and is *inconclusive* otherwise. The spec decides what inconclusive
exits with (`on_inconclusive`, default `pass`).

## Considered options

- **A `--min-score` flag (#197).** Rejected as the gate. A flag lives in a
  workflow file or a shell history, can be changed in the same PR as the
  skill, and can be tuned after looking at the number. The spec field gives the
  bar one committed home with a history.
- **A separate policy file.** Rejected. A second file has to be kept in step
  with the spec's tasks; a bar names rates of exactly those tasks. The bar does
  not depend on the engine, so a spec field does not conflict with ADR 0004.
- **Compare the point estimate with the bar.** Rejected. At CI sample sizes
  (k = 3–5) 4/5 and 5/5 are both likely draws from the same skill, so a point
  rule blocks on noise. A gate that fails at random gets ignored, and then it
  blocks nothing.
- **Block when inconclusive, by default.** Rejected. At small k most intervals
  straddle any useful bar, so nearly every run would block. A team that wants it
  opts in with `on_inconclusive: fail`.
- **Non-inferiority against the ablated skill as the default rule** (#73's first
  sketch). Deferred. It needs a second (ablated) run per check, which doubles CI
  cost. An absolute bar is the minimum that makes exit `3` real. A relative rule
  can be added to `bar:` later without breaking it.
- **Gate `compare` too.** Rejected. Its regression flag fires on any drop
  (docs/CONTEXT.md → Regression). A base comparison stays a report.

## Consequences

- **Exit `3` is live.** It is checked last, after every "could not run" exit, so
  a broken pipeline is never reported as a failing skill.
- **An ablated, interrupted, or hook-failed run is not gated.** The bar speaks
  about a clean run of the full spec. Those runs report the verdict as *not
  applied* and keep their own exit codes.
- **Saved runs record their bar** (`RunMeta.bar`). An old run is re-judged by
  its own bar, not by today's spec. The verdict is derived from the bar and the
  attempts and never stored.
- **The pooled rate can differ from the headline.** The headline averages
  per-task rates. The interval needs a trial count, so the bar pools attempts.
  They agree when every task ran to k with no unusable attempts.
- **A bar no task can produce is refused at load.** That is a `score:` bar on a
  spec of trigger probes, or an `activation:` bar with no `activates:`.
