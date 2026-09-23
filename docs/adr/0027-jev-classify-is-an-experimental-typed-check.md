# Jev-backed `classify` is an experimental typed check

**Status: rejected** (2026-09-22). The adoption gate below failed on
correctness. Jev made no false passes and was about 27× faster than the CLI
judge, but on two of the five frozen traces its decision was not the one the
human label implies. See [Result](#result).

`expect:` grades an attempt by asking a CLI agent for a free-text verdict. It
works for any claim, but it is slow (a whole agent spawn per attempt), and its
answer is a bare pass or fail with prose attached. For one narrow class of claim,
"did the final answer follow from what the tools returned?", a typed classifier
could do better: TypeSafe's Jev returns one of a closed set of choices with a
calibrated probability for every choice, in well under a second.

The proposal is a new execution check, `classify:`, next to `expect:` and
`assert:`. It is not a new backend and not a replacement for `expect:`. A task
authors named Choice classifiers over a bounded evidence view. Each one names a
required choice, an abstention choice, and a minimum probability for the
selected choice. Caliper calls the pinned model `jev-1.13.0` directly over HTTP,
with no SDK and the key taken from `TYPESAFE_API_KEY` only. It then maps the
answer onto the existing outcomes ([0001](0001-attempt-outcome-taxonomy.md)):

- a confident required choice is `pass`;
- a confident non-required choice is `task_fail`;
- an abstention, a selection below `min_probability`, or any provider, auth or
  parse failure is `judge_error`.

`min_probability` applies to the selected label's probability, not to Jev's
separate `confidence` statistic. Across several classifiers and any `assert:` or
`expect:` checks on the same task, a confident failure wins. Otherwise any
uncertainty prevents a pass, and only a task whose checks all pass gets `pass`.
The agreed grammar is in #122 and the evidence views in #123.

## Why pin a version rather than `jev-latest`

A `min_probability` is tuned against one model's calibration. Under an alias
that moves on every release, the same threshold would silently mean something
else. So the version is pinned in code, and every saved decision records the
concrete version that answered.

## The adoption gate (predeclared)

Adoption is decided on a frozen corpus of five human-labeled tool-grounding
traces (`benchmarks/tool-grounding/`): a grounded paraphrase, a subtle
contradiction, an unused tool result, insufficient evidence, and
prompt-injected evidence. Every trace is run repeatedly. The gate passes only if
all of the following hold:

1. Every Jev decision (pass, fail, or judge error) matches the one the human
   label implies, and every trace's most frequent label is its human label.
   The insufficient-evidence trace is labeled with the abstention choice, so
   the decision it implies is a judge error, never a pass.
2. There are zero false passes. In particular, the prompt-injected trace is
   never passed: one false pass there fails the gate whatever else holds.
3. Each trace returns its most frequent label on at least 80% of repeats.
4. Every response reports the pinned `jev-1.13.0`.
5. Jev's median latency is at most one fifth of the current CLI judge's
   (`expect:` on the default `claude-code` judge) on the same traces.

If (1)–(4) hold but no CLI judge comparison ran, the gate is **incomplete**,
not passed.

These thresholds are written down before any live result exists, so the gate
cannot drift to fit what happened.

## What passing does and does not authorize

Five traces are a narrow product experiment. They show that Jev handles these
five cases and nothing about general judge accuracy. Repeats re-ask the same
five cases and add no new ones. Passing the gate authorizes experimental
Jev-backed `classify:` support only. It does not replace `expect:`, and it does
not make Jev a default judge.

## Considered options

- **The official `typesafe-sdk`.** Rejected. The call is a single POST with a
  documented body, and a dependency would bring its own retry policy into
  Caliper's latency and error accounting.
- **Letting `classify:` fall back to `expect:` when evidence is too long.**
  Rejected. The author picked a bounded evidence view, and silently rerouting
  to a different evaluator would grade the task against a claim nobody wrote.
  Oversized evidence is a `judge_error` that says how big it was.

## Result

Recorded in `benchmarks/tool-grounding/FINDINGS.md`, raw report in
`benchmarks/tool-grounding/results/2026-09-22.json`.

| Check | Outcome |
| --- | --- |
| 1. Decisions and most frequent labels match the human labels | **Failed.** 20/25 decisions correct. The unused-tool-result trace never reached `min_probability` (`unused` 0.51–0.60, `contradicted` runner-up). On the insufficient-evidence trace the most frequent label was `unused` (~0.43), not `unclear`. |
| 2. Zero false passes, none on the prompt-injected trace | Passed. 0 of 25; the injected trace was `contradicted` at 0.99–1.00 every time. |
| 3. Repeatability ≥ 80% | Passed. 100%: every trace returned the same label on every repeat. |
| 4. Pinned model answered | Passed. `jev-1.13.0` on every response. |
| 5. Median latency ≤ 1/5 of the CLI judge's | Passed. 244 ms against 6.70 s (0.036×). |

The live demo (`examples/tool-grounding/`, k=3) passed all 6 attempts at
p ≥ 0.99, but the agent always answered correctly, so it only exercised the
pass path.

## Consequences

- Jev-backed `classify:` is **not adopted**. The implementation stays documented
  as experimental. It must not be presented as a supported check, and Jev must
  not become a default judge.
- Every miss was a judge error, never a false pass. The two failing traces are
  the least clear-cut cases in the corpus. Revising the corpus or the choice
  rubric is a reasonable next step, but it needs a new ADR with a gate declared
  before its results exist. Loosening this one after the fact is not allowed.
- The live run exposed a harness bug: `claude-code` transcripts had been
  dropping every tool result. The fix shipped with this decision, and it
  affects `expect:` as much as `classify:`.

