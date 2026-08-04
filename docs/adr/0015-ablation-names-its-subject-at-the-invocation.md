# Ablation names its subject at the invocation

`--baseline` is retired and replaced by `caliper run --ablate <skill-name>`
(repeatable), which installs the [[skill neighbourhood]] *minus* the named
skills and saves **one ordinary run**. The delta is read afterwards with
`caliper compare`. This deletes `RunResults.baseline_task_results`,
`compare.diff_baseline`, and the bespoke rendering seam in `reporter.py` that
existed only to re-attach the activation aggregate to a `--baseline` report.

The load-bearing observation is that **an ablated arm is a property of the tasks
and the surviving neighbourhood, never of the ablated skill's text.** The skill
is not installed, so neither its body nor its `description` can move that number.
`--baseline` re-ran it on every invocation, re-measuring — at full price —
something that by construction could not have changed. Saved as a run in its own
right, it is paid for once and re-compared against every later iteration of the
skill.

That reframing is also what makes leave-one-out affordable. It read as "N+1 arms
per run" and was rejected on cost; as a reusable saved run it costs exactly what
the bare-agent arm costs and answers a strictly better question, so the flag
takes a **skill name** rather than being a boolean.

Naming the subject at the invocation does not reopen
[0014](0014-activation-is-a-check-type-not-a-separate-command.md). That ADR
rejected a subject **in the spec** — `- path:` mappings and a
first-entry-is-subject rule — because positional subjecthood makes a silent
reorder re-point what is being measured. `skills:` remains a list of peers.
Subjecthood is a *runtime axis*, exactly as the engine is
([0004](0004-engine-is-a-runtime-axis-not-a-spec-field.md)): chosen per
invocation, recorded in `RunMeta`, absent from the file.

## Context: why `--baseline` stopped earning its cost

Before [0013](0013-install-and-discover-is-the-only-loading-discipline.md) most
backends force-loaded the skill, so `--baseline` compared "skill body in context"
against "no skill" — a clean question. Under install-and-discover the with-skill
arm may never activate, so the delta conflates *did the agent reach for it* with
*did it help once reached*. When the skill does not activate, both arms are the
bare agent and the delta is structurally ~0 — which reads as "the skill adds
nothing" when the truth is "the description never fired." The `activated` column
already distinguishes those, more precisely and for free.

Activation does **not** subsume the capability, though. It never answers "would
the bare agent have passed anyway", and a task the base model passes regardless
is a real finding. That question survives here — it is the ablate-everything
case — and is now reusable rather than re-billed.

## Considered options

- **Keep `--baseline` and re-scope the docs** ("run it when the tasks change, not
  when the skill changes"). Rejected: it enforces by prose what a flag shape
  enforces by construction, and leaves the schema field and the rendering seam in
  place to do it.
- **A boolean `--no-skills` producing one saved run.** This was the intermediate
  design and it is strictly worse than `--ablate <name>`: for a singleton
  neighbourhood — every spec in this repo, and what `grill-skill` generates —
  `--ablate my-skill` *is* the bare-agent run, so the general flag costs nothing
  extra in the common case while also expressing leave-one-out.
- **Both `--ablate <name>` and `--no-skills`.** Rejected: two ways to say one
  thing for every spec that actually exists. Enumerating the neighbourhood for
  the bare case is honest work, and a stale enumeration fails loudly at
  `resolve_skills` rather than quietly measuring the wrong thing. Widening to an
  optional-value `--ablate` later is non-breaking.
- **Keep `--baseline` as sugar for "ablate everything".** Rejected as the worst
  option. `--baseline` produced *two* arms in one invocation; `--ablate` produces
  one. A scripted `--baseline` would silently cost half as much, emit a single
  score table instead of a diff, and stop rendering the delta its caller was
  reading — a silent semantic change under a stable name, the same class of thing
  as the mangled `SKILL-vrd-<uid>` identity 0013 removed. The flag is instead
  kept parseable for one release as a **stub that errors** with a pointer to
  `--ablate`; caliper ships on PyPI, and typer's bare `No such option` would tell
  an outside caller nothing about where the capability went.
- **Filtering `activates:` to the surviving skills and scoring it.** Rejected —
  see Consequences.
- **Inferring an ablation pair in `compare` from B's neighbourhood being a strict
  superset of A's.** Rejected on 0014's own precedent for the era marker: this is
  a semantic fact that merely *correlates* with a schema shape today, and the next
  schema change would silently break the inference. `RunMeta` records `ablated:`
  explicitly.

## Consequences

- **An ablated run drops every task's activation expectation; it does not filter
  it.** `activation_expected` is `None` for the whole run, so the scoreboard
  renders *skipped*, never `0%`, while the observation itself is still recorded
  and displayed. Filtering `activates: [x, y]` down to `[y]` under `--ablate x`
  would have caliper assert a claim the author never wrote, and it inverts the
  delegating case that 0014's exact-set-match was designed for: ablate
  `grill-with-docs` from `activates: [grill-with-docs, grilling,
  domain-modeling]` and its neighbours correctly stop firing, because nothing
  delegates to them any more — scoring that as a miss reports the finding as a
  failure. Corollary: `validate_activates` must not refuse an `activates:`
  naming an ablated skill — the expectation is dropped, not violated.
- **The observation needs its own rendering, on the skill axis.** Dropping every
  expectation empties the scored activation table, and a fully-passing task earns
  no failure panel — so without something new, "with the parent removed, did its
  neighbours pick up the work?" would be invisible on exactly the run that exists
  to ask it. An ablated run therefore prints per-skill **observed** counts
  (`fired/observed`), including a row for a declared skill that never fired,
  whose dormancy is itself an answer. Deliberately *not* the scored
  `SkillActivationStats` type: with nothing expected its recall is undefined and
  its unwanted rate would read 100% — "fires when not wanted" — for a skill that
  fired exactly when a reader would hope. Nothing was *wanted* because nothing
  was asserted, which is a different claim. Its denominator is
  `activation_observed` (a whole transcript) rather than `activation_scored`
  (which additionally requires an assertion there is none of.)
- **`RunMeta` gains `ablated: list[str]`, making a saved run self-describing.**
  An empty `skill_snapshots` list is otherwise ambiguous between "ablated
  everything" and "declared no skills". The marker also lets `diff_runs` derive
  side labels itself (`without grilling` vs `full neighbourhood`), so the one
  genuine contribution of `diff_baseline` — its `no skill` / `with skill`
  labelling — survives the deletion of the bespoke path instead of being a
  casualty of it. It further makes a diff of two runs that ablated *different*
  skills catchable; nothing else could catch it.
- **`compare` stops warning `neighbourhood_mismatch` on a recognised ablation
  pair.** The differing neighbourhood *is* the experiment there. Today
  `diff_baseline` achieves this by handing `_diff` the same neighbourhood for
  both sides — harmless only because a bespoke path controlled both ends, and not
  available once the arms are two independent saved runs.
- **Removing `baseline_task_results` is load-safe.** The results models set no
  `model_config`, so pydantic v2's `extra="ignore"` parses older JSON unchanged.
  Empirically nothing is lost: of the 18 saved runs in this repo, 8 carry the key
  and **none** has it populated — no `--baseline` run was ever saved here.
- **Leave-one-out is now expressible, and per-skill contribution becomes an
  N-run question.** Measuring what each of N neighbours contributes takes N
  ablated runs. Each is reusable, so the cost amortises, but nothing here batches
  them into a single invocation and no `--ablate-each` sweep is implied.
