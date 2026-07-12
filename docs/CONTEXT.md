# Caliper — Domain Glossary

This file is a glossary only: canonical terms and their meanings. No
implementation details, no specs, no decisions (those live in `docs/adr/`).

## Eval spec

The `.eval.yaml` file. It describes **what** is tested and **how success is
judged** — the task prompts, `expect:`/`assert:` checks, `sandbox.forbidden_files`,
and `skill.path` — and nothing about *which engine runs or grades* it. The
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
with memory/persona/rule injection switched off, so the only skill present is the
[[eval spec]]'s skill-under-test. Normalization also extends to the tool
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
  ClawHub user runs it), not injected — see [[load-a-skill-natively]] for why
  "native" loading is unavailable here.
- Its `--json` output may expose only the final message, not the sub-agent's
  tool calls, limiting what `assert:`/`expect:` can inspect.

## Ablation

Holding the eval fixed while varying the *skill text*, then comparing the score
across variants — as opposed to `--baseline`, which varies skill-vs-no-skill.
Caliper has no native ablation mode: a variant is run by pointing the spec's
`skill.path` at a different `SKILL.md` (or swapping the file) and re-running.
Each run's `skill_snapshot` records the exact skill content + git SHA, so a
score is always traceable to the text that produced it.

## Run comparison (`compare`)

A side-by-side of two **already-saved** runs of the same eval — control vs.
candidate, or the same skill over time. `caliper compare <A> <B>` reports, per
matched task, each run's score, the signed delta, and both per-attempt strips,
plus a matched-only aggregate delta and a flag on any regressed task. It is the
reading half of an [[ablation]]: it never produces runs, only diffs saved ones.
Contrast `--baseline`, which is a within-run skill-vs-no-skill delta; a
comparison is across two independent runs.

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
still beat the `--baseline` (no-skill) score. Equalling or exceeding full-skill is
a bonus, not required. We are proving *no worse*, not *better*.

## Single-shot harness

Every backend's `HarnessBackend.run(...)` takes one `prompt` and returns one
transcript; nothing ever answers the agent's questions back. There is no
multi-turn / simulated-user turn-taking. Consequence: interview skills (their
value is the back-and-forth) can only be tested on their **first-turn
discipline**, not a full simulated conversation.

## Skill-directory staging

When a skill is a **directory** (a file named `SKILL.md` with siblings), the
runner stages that directory's contents into the run's working dir
(`isolated_home`, every CLI backend's `cwd`) before the harness runs — so
relative pointers like `[REFERENCE.md](REFERENCE.md)` and `references/` resolve,
mirroring how a skill is really installed. One copy covers `claude-code`, `codex`
and `pi` at once. Cheat surfaces are never staged: the `.eval.yaml` spec,
`.caliper/` results, and anything the spec marks `forbidden_files` are excluded,
so staging cannot leak the answer key. A **lone** slash-command `.md` file (not
named `SKILL.md`) has no skill directory and is injected text-only, unchanged.

Consequence: **progressive disclosure is measurable** — moving prose out of
`SKILL.md` into `REFERENCE.md` is testable, because the agent can actually reach
the referenced file during a run. (Every backend is now a CLI agent that can
reach staged files; the tool-less `claude-api`/`openai-api` backends, which
could only inject `SKILL.md` text and so couldn't measure disclosure, were
removed — see [[cli-agent-backends-only]].)

## Load a skill *natively*

To hand the skill-under-test to the agent through the agent's **own
skill-loading flag** (e.g. pi's `--skill <path>`, the agentskills.io
standard), rather than pasting the skill's text into the prompt.

The contrasting term is **inject**: paste `SKILL.md` into the prompt/message
because the agent has no native skill-loading mechanism. `codex` injects;
`pi` loads natively. "Support X natively" therefore means "X exposes a
skill-loading flag we can pass," not merely "we added an X backend."

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

`passed` is retained as a derived convenience, equal to `outcome == pass`.

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
