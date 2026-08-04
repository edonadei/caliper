# Caliper — Domain Glossary

This file is a glossary only: canonical terms and their meanings. No
implementation details, no specs, no decisions (those live in `docs/adr/`).

## Eval spec

The `.eval.yaml` file. It describes **what** is tested and **how success is
judged** — the task prompts, the `expect:`/`assert:`/`activates:` checks,
`sandbox.forbidden_files`, and the `skills:` [[skill neighbourhood|neighbourhood]]
(a list of paths; a task must carry at least one of the three checks) — and
nothing about *which engine runs or grades* it. The
[[engine as runtime axis]] (backend + model, for both the skill-under-test and
the judge) is deliberately absent from the spec: it is chosen at invocation, not
authored into the file. Consequence: a spec never ages when a model goes stale,
and the same spec can be pointed at any agent without editing it (see
[[cli-agent-backends-only]] for the sibling decision that shrank the backend set).

## MCP server (declared)

An MCP server the agent-under-test is given access to, declared in the [[eval
spec]]'s top-level `mcp:` block (a mapping keyed by server name). It is a
*capability granted to the agent-under-test for the eval* — part of the run
environment, the sibling of `sandbox:` (which takes capabilities away where
`mcp:` adds them) — not a property of the invocation, which is why it lives in
the spec beside `sandbox:` and not in the [[engine as runtime axis]] (see
[[0008-mcp-servers-are-a-spec-field]]). It is deliberately *not* nested under
`skill:`: it applies to the agent whether or not the eval uses a skill (a
bare-agent run can still declare `mcp:`). A declared server's credentials are
supplied by reference to a host environment variable, never written into the
committed spec (see [[0009-mcp-secrets-interpolated-at-the-harness-boundary]]).
On `claude-code` both *local stdio* and *remote (HTTP/SSE)* servers are honored;
the agent's call appears in the transcript as `mcp__<server>__<tool>`. `hermes`
also honors `mcp:` (stdio, plus remote *header-auth*; remote *OAuth* is out of
reach because it needs an interactive browser flow the harness cannot drive),
translating the block into its native `mcp_servers` config key inside the
isolated `HERMES_HOME` — where the same call surfaces under hermes' own naming,
`mcp_<server>_<tool>` (single underscores), not claude-code's doubled form. So a
spec must never hard-code one backend's tool-name spelling in `expect:`/`assert:`
if it is meant to run under any engine ([[engine as runtime axis]]). `codex`
also honors `mcp:` (stdio, plus remote *header-auth*; remote *OAuth* is out of
reach for the same interactive-browser reason as hermes, and caliper's spec
cannot express an OAuth remote anyway), translating the block into
`[mcp_servers.*]` tables inside the isolated `~/.codex/config.toml` — a stdio
server as `command`/`args`/`env`, a remote server as `url` plus a static
`http_headers` map of boundary-resolved literals (codex infers its one
streamable-HTTP transport from the presence of `url`, so caliper's `type:
http`/`sse` distinction collapses onto it). The call surfaces with
codex's `mcp__<server>__<tool>` naming — the *same doubled-underscore form* as
claude-code, unlike hermes' single-underscore spelling. Because codex seeds each
attempt from the user's real `~/.codex/config.toml`, its `[mcp_servers.*]` tables
are normalized to *exactly* the declared set (an empty set when no `mcp:` block),
so an attempt never inherits the user's ambient personal MCP servers — the same
tool-environment neutralization hermes performs, needed here despite codex being
stateless because the leak comes from seeding the real config, not from agent
state. `pi` will *never* honor `mcp:`
natively — its agent has no MCP by design (see [[pi-mcp-unsupported-by-design]]),
so caliper refuses an `mcp:` spec on `pi` with guidance to wrap the server as a
CLI tool the skill drives, or as a pi extension. `pi` is now the only backend
that refuses `mcp:`, and its refusal is permanent by design — not the
"not-yet-implemented" placeholder codex used before it gained support.
_Avoid_: MCP config, tool server.

## Engine as runtime axis

The **backend + model** used to run the skill-under-test (or to grade it) is a
*swept axis of an invocation*, not a property of the [[eval spec]]. It comes from
`--model` / `--judge-model` (or their default, `claude-code`), and the actual
engine that produced a result is recorded per run in `RunMeta` — both the skill
`backend`/`model` **and** the `judge_backend`/`judge_model` that graded it, and
(wherever the backend reports it — the skill model from hermes' session export,
the `judge_model` from the claude-code judge's JSON) the concrete model a
default-model run resolved rather than a bare "default" — so de-pinning never
costs reproducibility. Corollary: a spec cannot express "this is meant for
`codex`/`pi`"; a non-default backend must be named at every invocation until a
project-level default lands (the unified-harness-config direction).

## Backend (a.k.a. harness)

An adapter that runs the skill-under-test once and returns an `AttemptResult`
(transcript + final output + exit code). Each backend implements
`HarnessBackend.run(...)`. Current backends: `claude-code`, `codex`, `pi` — all
CLI agents that can actually load and run a skill. Direct API access is *not* a
backend: to run against API-priced billing you configure one of these CLI
harnesses with an API key (see [[cli-agent-backends-only]]).

## Flat backend (executor)

A backend where a single agent runs the skill directly, so the score
reflects *that agent's* reliability at the skill. All current backends are
flat.

## Stateful backend

A backend whose underlying agent carries **persistent state across invocations**
— cross-session memory, a personality/persona file, an auto-generated skill
store — so that, run as its user really runs it, its attempts are *not*
independent. `hermes` (proposed) is the first: it injects a `SOUL.md` persona and
an always-on `MEMORY.md` into every turn and auto-generates skills. Because a
score is only meaningful when the k attempts are independent (see
[[single-shot harness]]), a stateful backend is only admitted after being
**normalized** to a neutral agent: each attempt runs in an isolated agent home
with memory/persona/rule injection switched off, so the only skills present are
the [[eval spec]]'s declared [[skill neighbourhood]] — the closure that makes
[[activation]] measurable. Normalization also extends to the tool
environment: hermes' `mcp_servers` is set to *exactly* the spec's declared
[[MCP server (declared)|servers]] — an empty set when the spec declares no
`mcp:` — so an attempt never inherits the user's ambient personal MCP servers
from the seeded config. Normalized, it is a [[flat backend]] like any
other. The contrast is with `claude-code`/`codex`/`pi`, which are stateless by
default and need no normalization.

## Orchestrator backend

A backend whose agent does not run the skill itself but **routes** it to an
underlying coding agent (the *sub-agent* / *worker*) and reports back.
`openclaw` (proposed) is the first of these: it delegates to Claude Code /
Codex / OpenCode. Consequences that distinguish it from a flat backend:

- Its score conflates the skill + orchestration + the sub-agent, so the
  number is **not apples-to-apples** with a flat backend's. The sub-agent
  must be *pinned* for the number to be reproducible.
- The skill is **installed** into the orchestrator's registry (the way a real
  ClawHub user runs it) — the same discipline every backend now follows, see
  [[install-and-discover]].
- Its `--json` output may expose only the final message, not the sub-agent's
  tool calls, limiting what `assert:`/`expect:` can inspect.

## Ablation

**Removing** a declared member of the [[skill neighbourhood]] for one run —
holding the [[eval spec]] and the [[engine as runtime axis|engine]] fixed — so
that the score difference measures what that member contributed.
`caliper run --ablate <name>` (repeatable) installs the neighbourhood *minus*
the named skills and saves an ordinary run; the delta is read afterwards with
[[run comparison|`caliper compare`]]. Ablating every declared member leaves the
bare agent.

**Which skill is the subject is a property of the invocation, not the spec** —
the same discipline the [[engine as runtime axis|engine]] follows. `skills:`
stays a list of peers with no privileged entry, so a reorder can never re-point
what is being measured.

An ablated run is a property of the **tasks and the surviving neighbourhood**,
never of the ablated skill's text: that skill is not installed, so neither its
body nor its `description` can move the number. It is therefore run **once** and
re-compared against every later iteration of the skill. A task the bare agent
already passes is a finding about the *task*, not about the skill.

Varying a skill's *text* across runs is not ablation — it is two runs and a
[[run comparison]]. Each run's skill snapshots record the exact content + git SHA
of *every* member of the neighbourhood it installed — plural, because a
neighbour's `description` is part of what produced the score, so a run is not
reproducible without it.
_Avoid_: baseline (the retired `--baseline` flag ran both arms inside a single
invocation, and so re-paid for the removed arm on every run).

## Run comparison (`compare`)

A side-by-side of two **already-saved** runs of the same eval — control vs.
candidate, or the same skill over time. `caliper compare <A> <B>` reports, per
matched task, each run's score, the signed delta, and both per-attempt strips,
plus a matched-only aggregate delta and a flag on any regressed task. It is the
reading half of an [[ablation]]: it never produces runs, only diffs saved ones.
There is no within-run diff — an ablated arm is an ordinary saved run, so
skill-vs-no-skill and candidate-vs-control travel one path rather than two.

A run records which skills it [[ablation|ablated]], so a deliberate ablation pair
is recognised and labelled from that marker rather than inferred from its smaller
neighbourhood — the same reason the era marker is explicit rather than sniffed
from a schema shape. Comparing two runs that ablated *different* skills is caught
on the same marker.

## Task identity

What lines a task up **across two runs**. The stable identity is the task's
**`task_name`** (the authored `name`). `task_id` is *not* an identity: today it
is assigned positionally at load (`task-001`, `task-002`, …), so it changes when
tasks are reordered or inserted. Comparison therefore matches on `task_name`,
using positional `task_id` only to disambiguate duplicate names. A task present
in only one run is **unmatched**.

## Success rate (the score)

The **primary metric**: the raw per-attempt success rate, `successes / usable`
(how often a *single* run works), computed over [[usable / unusable
attempt|usable]] attempts only. It is `TaskResult.score` and the aggregate
`avg_score`, and it is what every table headline, the `Δ`, and [[regression in a
comparison|regression]] are computed on. Chosen over pass@k because Caliper tests
*reliability*: pass@k (below) is a code-generation metric that rewards retries and
flatters flaky skills (`1/3 → 70.4%`), which is the wrong question when a skill
runs once in production.

**pass@k** and **pass^k** are kept as *secondary* views — on every task in the
JSON (`pass_at_k` / `pass_hat_k`) and under `--verbose`. `pass@k = 1−(1−p)^k` is
P(≥1 of k pass) — retry-optimistic; `pass^k = p^k` is P(all k pass) — the strict
consistency view. Both are monotonic transforms of the score at a fixed k, so
they move with it (though they can disagree in sign across sides with different
*usable* counts — another reason the raw score is the canonical comparison basis).

## Regression (in a comparison)

For a matched task, B is a *regression* when its [[success rate|score]] is
**below** A's by any amount (any-below rule). The signed delta is always shown;
the regression flag fires strictly on `B < A`. This is deliberately coarser than
the [[non-inferiority-bar]] (which tolerates a ≤5% margin) — a margin is a
possible later refinement, not the initial rule.

Regression is a **score-only** verdict. [[run usage totals|Token and wall-clock
deltas]] are shown alongside it — coloured green when B is *cheaper* (fewer
tokens / less time) and red when costlier — but they never feed `has_regression`:
a token drop is a win, not a failure, and a token rise at an equal score is a
trade-off to weigh, not a regression to flag. Only the score gets the bold
regression treatment.

## Non-inferiority bar

The decision rule for accepting a shortened variant: at k≥5 its [[success
rate|score]] must be within a small margin (≤5%) of the full skill's score **and**
still beat the score of the same tasks with that skill [[ablation|ablated]].
Equalling or exceeding full-skill is a bonus, not required. We are proving *no
worse*, not *better*.

## Single-shot harness

Every backend's `HarnessBackend.run(...)` takes one `prompt` and returns one
transcript; nothing ever answers the agent's questions back. There is no
multi-turn / simulated-user turn-taking. Consequence: interview skills (their
value is the back-and-forth) can only be tested on their **first-turn
discipline**, not a full simulated conversation.

## Activation

The agent, offered a skill that is **installed but not preloaded**, chose to
bring it into its own context. It is an *observation of the agent's judgement*,
and it is the thing `activates:` asserts on.

Three neighbouring words that are not this one, and are worth keeping apart:

- **Loading** is the *mechanism* — how a skill becomes reachable at all. It is
  caliper's job, and under [[install-and-discover]] it is the same every run.
- **Triggering** is the `description`'s job — the property of the *text* that
  makes an agent reach for it. Activation is the evidence that triggering
  worked, on one prompt, once.
- **Execution** is what happens after: whether the skill's *body* got the task
  done. Activation and execution are measured on the same attempt but scored on
  [[activation score|separate scoreboards]], because their failures have
  opposite fixes — a bad `description` is edited in the frontmatter, a bad body
  in the prose.

Every attempt yields an activation observation as a by-product, whether or not
the task asserts on it.
_Avoid_: firing, invocation, triggering (when you mean the observation).

## Activation score

The second scoreboard, scored per attempt as an **exact set match**: the
observed [[activation]] set equals the set the task's `activates:` declared,
or the attempt fails. It never merges with the [[success rate|execution
score]] — two aggregates, two regression flags — because blending them would
make the headline a mix of "does the body work" and "does the description
fire", and those have opposite fixes.

Its denominator is **not** the execution one. An attempt is activation-usable
unless it was an `infra_error` or a `timeout` — the two cases where the
transcript may be truncated and an empty observed set would be a fabricated
negative. A `judge_error` *is* activation-usable: the agent ran, the transcript
is whole, and only the grader broke. The two scoreboards therefore report
different counts, and nothing should assume they match.

Exact match means a skill that legitimately delegates has its whole chain
enumerated (`activates: [a, b, c]`), which makes "did it actually delegate?"
assertable. This is affordable only because the [[skill neighbourhood]] is
closed: an undeclared skill is not installed and cannot activate, so the
enumeration is bounded by a list the author wrote.

Alongside it, per-skill **recall** and **precision**, counted over attempts:
recall is how often a skill fired when it was expected, precision how often it
was expected when it fired. They are indexed by skill name rather than by role,
because the thing an author edits in response is one skill's `description`.

Both are *reported* in plain language rather than by their statistical names,
as "fires when wanted" and "fires when not wanted", because the reader is a
skill author diagnosing their own `description` and not an ML practitioner. The
pair deliberately shares one verb over two populations, so neither column needs
a word borrowed from the other to parse; the second is then good-when-*low*,
which the colouring carries. The terms stay `recall`/`precision` in the schema
and in this glossary, where exact language is the point.

The reported second direction is the **unwanted rate**, not `1 − precision`.
Precision divides by the times a skill fired; this divides by the times it
*should not have*. A skill that fires once wrongly across fifty silent
opportunities has 50% precision and a 2% unwanted rate, and the second is the
honest description of that skill. It is `None` when every attempt wanted the
skill, since it then had no opportunity to over-fire — which is not the same as
never taking one.

The two scoreboards are **not peers in the report**. The [[success rate|score]]
is the headline, because it is already the joint measure of "would this work for
a user"; activation is the *diagnostic* that says which half moved when the
score drops. Rendering them as two equal bars invites averaging them, which is
meaningless — they are rates over different populations. Activation is also
reported on its own axis: the score is per **task**, the activation stats are
per **skill**, and forcing both into one table is what makes either unreadable.

Not asserting `activates:` leaves `activation_passed` at `None` and renders
*skipped*, never `0%` — as does an [[ablation|ablated]] run, which **drops** every
task's expectation rather than filtering the ablated name out of it. Filtering
would have caliper assert a claim the author never wrote, and it inverts the
delegating case: remove a parent skill and its neighbours correctly stop firing,
so scoring that as a miss would report the finding as a failure. What each attempt
actually reached for is still observed and shown; only the verdict is withheld.

## Skill neighbourhood

The set of skills declared by an [[eval spec]] and installed for a run. Its
members are **peers**: no entry is privileged as "the skill under test", so
every claim about what fired names skills explicitly and never leans on
position or an implied default. The neighbourhood is deliberately closed — the
run's isolated home contains these skills and nothing else — which is what makes
[[activation]] a *measurement* rather than an observation: the set of things the
agent could possibly have reached for is exactly the set the spec wrote down.
Neighbours are not decoration; they are the competition a skill's `description`
has to win against, so a run's numbers are only meaningful relative to the
neighbourhood that produced them. A single run may install a strict *subset* of
it (see [[ablation]]); which member is left out is named at the invocation and
never in the spec, so the declared set stays the same peers whatever is being
measured. That is why a report names **every** declared
member, including one that never fired and was never expected: without its row a
reader could not tell what was installed, and its dormancy is itself an answer,
saying the probes never exercised the neighbour they were written to guard
against.
_Avoid_: skill list, skill dependencies, sibling skills.

## Install-and-discover

Caliper's **only** loading discipline: every member of the [[skill
neighbourhood]] is copied to the backend's native skills root
(`<skills_root>/<name>/SKILL.md`, the name taken from frontmatter), and nothing
is ever placed into the agent's context for it. The agent meets each skill the
way a real user's agent does — as a name and a `description` it may or may not
reach for. See [[install-and-discover-is-the-only-loading-discipline]].

The contrasting term is **preload**: put the skill's text in front of the agent
before it has decided it wants it — pi's `--skill`, hermes' `--skills` (whose
own help text says "preload"), or codex's invented `[Skill context]` prompt
prepend. Caliper does none of these. Preloading and [[activation]] are mutually
exclusive by construction: a skill already in context cannot be *chosen*.

Two consequences worth stating in the same breath. **Identity is established,
not discovered** — because caliper is the installer, the backend reports back
exactly the frontmatter `name:` the spec wrote, which is what lets an
observation be matched to a spec entry at all. And **cheat surfaces are never
installed**: the `.eval.yaml` spec, `.caliper/` results, `.git/`, and anything
the spec marks `forbidden_files` are excluded for *every* member — a
neighbour's answer key is still an answer key.
_Avoid_: inject, force-load, load natively (the old inject-vs-native axis is
retired; the axis is now install vs preload).

## Progressive disclosure

Writing a short `SKILL.md` that points at `REFERENCE.md`, `references/` and
helper scripts the agent reads on demand, rather than one long file. It is
**measurable** because the relative pointers resolve: the skill is installed as
a whole directory, so the agent can reach the referenced files during a run
exactly as it would from a real install. (Every backend is a CLI agent that can
reach them; the tool-less `claude-api`/`openai-api` backends, which could only
paste `SKILL.md` text and so could not measure disclosure, were removed — see
[[cli-agent-backends-only]].)

A **lone** slash-command `.md` file (not named `SKILL.md`) has no directory, no
frontmatter `name:` and no `description:` — nothing to install and nothing for
an agent to discover — so it is rejected at `validate` rather than measured as a
guaranteed zero.

## Outcome

The typed result of a single **attempt**, replacing the bare `passed: bool`. One
of six values, classified once at the seam where an attempt is assembled:

- `pass` — the attempt satisfied the task's judge(s).
- `task_fail` — the skill genuinely failed the task.
- `judge_error` — the judge could not produce a verdict at all (unparseable
  autorater response, or the judge call threw — including the judge's *own*
  rate-limit).
- `infra_error` — the skill-under-test's harness failed the attempt: nonzero
  exit (non-timeout), or a detected transient throttle/overload signal
  (spending cap, rate limit) even on a zero exit.
- `timeout` — the attempt exceeded its time budget with no usable result.
- `cheat` — a forbidden-file read was detected.
- `not_checked` — the attempt ran cleanly and the task authored **no execution
  check** (a [[trigger probe]]). Not an error and not a failure: nothing was
  asked, so nothing was answered.

`passed` is retained as a derived convenience, equal to `outcome == pass`.

Outcomes answer *two* questions, not one. **Usable** asks "does this count
toward the [[success rate|score]]?"; **execution noise** asks "should this be
*reported* as a problem?". They coincide for every value except `not_checked`,
which is excluded from the denominator without being an error — so a correct
trigger-probe spec never reads as broken.

## Trigger probe

A task that authors `activates:` and no `expect:`/`assert:` — its whole claim is
about **which skills the agent reached for**, not about the work. Two shapes:
a *neighbour probe* (`activates: [x]`, asserting the subject must not hijack a
prompt belonging to `x` — grading whether `x` did the job well is `x`'s own
eval) and a *silence probe* (`activates: []` on unrelated work, where there is
nothing meaningful to expect). It skips the judge entirely, so it is much cheaper
than an execution task, and renders as *trigger only* rather than a zero.
_Avoid_: activation-only task, triggering test.

## Attempt usage

The **token accounting** of a single attempt, carried on `TokenUsage` (a
submodel on both `AttemptResult` and `AttemptRecord`, mirrored so the
harness→runner mapping is a literal pass-through). All fields are optional — a
backend that cannot report them leaves them `None`, exactly like
[[engine as runtime axis]]'s `resolved_model`. Fields: `input_tokens`
(non-cached prompt), `output_tokens`, `cache_read_tokens`,
`cache_creation_tokens`; `total_tokens` is computed as the sum of all four
(the honest volume of work done).

**The four fields are disjoint** — `input_tokens` is *non-cached* prompt tokens
only, cache lives solely in `cache_read_tokens`/`cache_creation_tokens` — so their
sum never double-counts. This is a normalized contract, not each CLI's raw
numbers, because backends disagree: claude, pi and hermes report `input` already
*excluding* cache (pass through), but codex uses OpenAI semantics where
`input_tokens` *includes* `cached_input_tokens`, so its `_usage` **subtracts**
(`input = raw.input_tokens − raw.cached_input_tokens`, `cache_read =
raw.cached_input_tokens`). codex has no cache-creation notion (→ `None`) and folds
`reasoning_output_tokens` into `output_tokens` (OpenAI counts it there).

**Dollar cost is deliberately out of scope.** Caliper tracks token *volume* and
[[wall-clock time]], not money: cost is inconsistent across backends (some report
actual, some only an estimate, some nothing) and would drag in a price table to
maintain. Tokens are the volume signal a reader actually acts on ("same quality,
40% fewer tokens"); a dollar figure can be derived downstream from tokens if ever
needed, without Caliper owning pricing. So `TokenUsage` has no `cost_usd` field.

Scope boundary: usage measures **only the skill-under-test's harness run**, never
the judge's autorater call — the tokens shown are skill-run tokens, not total
including grading. Judge usage is a deliberate later-follow-up, tracked
symmetrically if ever added.

## Wall-clock time

The already-captured `AttemptRecord.duration_seconds` — wall-clock seconds around
the harness's `_execute`. It is **not** part of [[attempt usage]]: it is universal
(every backend times its run) whereas tokens are optional, so it stays a
top-level field. It sits alongside tokens as the latency half of the cost/latency
axis: a skill edit that holds the score while cutting tokens *or* wall-time is a win.

## Run usage totals

The per-run roll-up of [[attempt usage]] and [[wall-clock time]], **derived** at
render time by summing over the run's `AttemptRecord`s — never stored on the
schema (mirrors how [[run comparison]] keeps usable/unusable counts derivable
rather than persisted). Every attempt counts toward the run total, because the
tokens and time were really spent; but the slice belonging to [[usable / unusable
attempt|unusable attempts]] is reported on its own line ("unusable spend") so a
timed-out attempt's wasted tokens/time are visible without distorting per-attempt
economics. The per-attempt average is taken over usable attempts only, matching
the score denominator.

## Usable / unusable attempt

An attempt that got a **fair shot** at the task is *usable*: `pass`, `task_fail`,
and `cheat` all count. `judge_error`, `infra_error`, and `timeout` are *unusable*
— the skill was never fairly measured — and are **excluded from the score
denominator**, reported instead as a separate "unusable attempts" count. A
throttled or judge-flaked run therefore can no longer masquerade as a
regression.
