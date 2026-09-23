# Jev-backed `classify` is an experimental typed check

**Status: proposed.** It becomes accepted or rejected when the adoption gate
below has been run and its result recorded here.

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
