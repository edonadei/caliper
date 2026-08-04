# Activation is a check type, not a separate command

`activates:` joins `expect:` and `assert:` as a third kind of check on a
`TaskSpec`, which now requires at least one of the three. There is no `caliper
trigger` command. Once [0013](0013-install-and-discover-is-the-only-loading-discipline.md)
stopped preloading, **every attempt already produces an activation observation
as a by-product**, so a task can assert on triggering and execution from a single
agent run — one run, two assertions, no double billing. This reverses the
original constraint in issue #18, and the reversal only makes sense downstream of
0013.

`skills:` replaces `skill: path:` — a set of paths, not one path. It is
run-environment state like `sandbox:` and `mcp:` (the framing established in
[0008](0008-mcp-servers-are-a-spec-field.md)), so it is shared by all three kinds
of check rather than split across two files.

## Considered options

- **A separate `caliper trigger` command.** Rejected: it would re-run the agent
  to learn something the `run` transcript already contains, doubling spend to
  produce a number that is *less* trustworthy, since the two runs would be
  different samples of the same stochastic choice.
- **`skills:` entries as mappings with a designated subject** (`- path: …` plus a
  first-entry-is-subject rule). Rejected on both halves. Positional subjecthood
  repeats a mistake the glossary already documents under *Task identity* —
  `task_id` is positional and therefore not an identity — and here a silent
  reorder would re-point what gets snapshotted, what `--baseline` is a baseline
  of, and what an [[ablation]] varies. With no subject to declare, `path:` became
  a single-key mapping in perpetuity, so entries are **bare path strings**;
  widening to `str | SkillEntry` later is non-breaking, so nothing is foreclosed.
- **Tolerating skill-invoked activations while asserting only on agent-chosen
  ones** (carried forward from #54, which this issue subsumes). Rejected as
  *unobservable*, not merely unimplemented. Nothing is preloaded, so a skill that
  delegates causes the agent to read its neighbour — which on the wire is a tool
  call byte-identical to any other agent-chosen activation. There is no causal
  marker on any of the four backends, so the rule could only be approximated by
  inferring causality from ordering, which is a heuristic dressed as a
  measurement. Authors enumerate the full chain instead
  (`activates: [grill-with-docs, grilling, domain-modeling]`), which costs a
  little repetition and buys "did it actually delegate?" as an assertable fact.
- **Per-entry expectation states** (required / forbidden / optional). Deferred.
  It is the honest answer to *conditional* delegation — a skill that reaches for
  a neighbour only sometimes will flake under exact match — but it weakens exact
  match into something that needs explaining. Filed as a follow-up to be
  triggered by observing the flake, not by anticipating it.

## Consequences

- **Two scoreboards that never merge, with different denominators.** Blending
  execution and activation would make the headline a mix of "does the body work"
  and "does the description fire" — two failures with opposite fixes. They also
  disagree on usability: an attempt is activation-usable unless it was an
  `infra_error` or `timeout` (the cases where a truncated transcript would
  fabricate an empty observed set), but a `judge_error` **is** activation-usable
  — the agent ran, the transcript is whole, only the grader broke. Reusing one
  rule would let a flaky autorater silently shrink the activation sample for a
  reason with no causal connection to it. The report must print both counts and
  nothing may assume they match.
- **Exact set match is affordable because the neighbourhood is closed.** The
  isolated home contains only declared skills, so the observed set is always a
  subset of `skills:` and enumeration is bounded by a list the author wrote. An
  undeclared dependency is not installed and cannot activate — which makes the
  choice visible in the spec: leave it out and you are testing the skill
  degraded, put it in and you must enumerate it.
- **No early stop on non-activation.** Unlike `infra_error`/`timeout`, a
  non-activation is *usable data*. Truncating after N misses biases marginal
  descriptions downward — a description with a true 20% rate reads as 0% about a
  third of the time — and manufactures false deltas in `compare`, which is
  caliper's core loop. Spend is controlled with `--k`. Early stop is a follow-up
  only if it carries a truncation marker that makes `compare` refuse the
  truncated side; never a silent one.
- **A seventh `Outcome`, `not_checked`, amending [0001](0001-attempt-outcome-taxonomy.md).**
  A task may now author `activates:` and nothing else — a *trigger probe*, whose
  whole claim is about what the agent reached for. Two shapes make this the
  natural form rather than a shortcut: a **neighbour probe** (`activates: [x]`
  asserting the subject must not hijack a prompt that belongs to `x`) has no
  execution outcome you care about, since grading whether `x` did its job well is
  `x`'s own eval; and a **silence probe** (`activates: []` on unrelated work) has
  nothing meaningful to `expect:`. Both are also far cheaper — no judge call at
  all — which is the premise the `--only trigger` follow-up rests on.

  Such an attempt reaches no judge, and labelling that `judge_error` made a
  *correct* spec report itself as broken while still paying for the judge call.
  `not_checked` says what actually happened: nothing was asked, so nothing was
  answered. It is excluded from the execution denominator like the unusable
  outcomes but is **not** noise, which is why `Outcome` now answers two questions
  instead of one — `is_usable` (counts toward the score?) and
  `is_execution_noise` (report it as a problem?). Consequences: the runner skips
  the judge entirely for these tasks; `TaskResult.usable` is derived from the
  attempts rather than `len − unusable`, which would over-count; wasted-spend
  accounting keys on noise, since a trigger probe's tokens bought a real
  activation measurement; and the row renders `—  —  trigger only` rather than
  `0/3 UNUSABLE`.
- **`compare` carries the activation delta and a separate regression flag.**
  "Separate aggregates *and separate regression flags*" is a `compare` concept —
  `has_regression` lives there. Without it the loop this feature exists to serve
  (edit a `description`, re-run, compare) has nothing to read, and a trigger
  probe — whose execution score is `None` by construction — would be a blank row.
  `TaskComparison` gains `a_activation`/`b_activation`/`activation_delta`/
  `activation_regression`, `RunComparison` gains `has_activation_regression`, and
  the two are reported on separate lines. A description that stopped firing and a
  body that stopped working are fixed in different places, so one flag covering
  both would name neither.
- **`activated` is `list[str] | None`, and its `None` is not `activation_passed`'s
  `None`.** `activated: None` means *not observed* (no detector, or a transcript
  that never arrived) and renders `—`, matching the idiom already used for
  `resolved_model` and `TokenUsage`; a bare `[]` would let an infrastructure
  failure render as `(none) 5/5` — a confident claim that a description never
  fires, manufactured from a timeout. `activation_passed: None` keeps the
  existing `assert_passed` meaning: *not asserted*, rendered *skipped*, never
  `0%`. Two different `None`s, each matching its own established idiom.
- **One detector in the runner; backends contribute facts, not algorithms.**
  Detection mirrors `_CheatDetector` — it inspects the already-normalized
  `ConversationTurn.tool_name`/`tool_input` that all four backends produce.
  Backends declare a `skills_root(ctx)` and their dedicated tool names (`Skill`,
  `skill_view`); the matching rule lives once. The rule is the **union** of both
  observable shapes on every backend — a dedicated skill tool call, *or* any tool
  call referencing `<name>/SKILL.md` — because an agent that reads the installed
  file has demonstrably brought the skill into context regardless of which tool
  it used. Matching is on the **suffix**, never an absolute path: the spike saw
  codex emit the same activation as an absolute path in one run and a relative
  one in the next.
- **`compare` gains two guards at two severities.** A cross-era diff (0013)
  **refuses**: same spec name, same k, same task names, a plausible-looking delta
  — meaningless output that invites no suspicion, which is what earns a hard
  stop. A **neighbourhood mismatch** merely warns, alongside `spec_mismatch` and
  `k_mismatch`: comparing `skills: [a]` against `skills: [a, b]` is confounded,
  because the larger neighbourhood gives the agent a competitor and some attempts
  never activate `a` at all — but the result is legible, and caliper should not
  refuse a diff the user understands better than it does. Note `spec_mismatch`
  compares the spec *name* and so cannot catch this. The era is recorded as an
  **explicit** marker rather than sniffed from `SkillSnapshot` having gone plural:
  era is a semantic fact that merely correlates with a schema shape today, and the
  next schema change would silently break the inference.
- **Per-skill recall and precision, counted over attempts.** Indexed by skill
  name rather than by role, since the thing an author edits in response is one
  skill's `description`. Per-attempt, not per-task, so the diagnostic shares
  units with the score above it.
- **`--baseline` skips activation entirely.** (Amended by
  [0015](0015-ablation-names-its-subject-at-the-invocation.md), which retires the
  flag in favour of `--ablate` and generalises this to *any* ablated run — which
  observes activation but withholds the verdict, since a partial ablation still
  has skills installed to observe.) It installs no skills, so scoring
  activation would be scoring caliper's own plumbing.
