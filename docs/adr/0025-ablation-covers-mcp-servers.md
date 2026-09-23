# Ablation covers `mcp:` servers, not only skills

[0015](0015-ablation-names-its-subject-at-the-invocation.md) made
`--ablate <name>` name its subject at the invocation and nothing in that
reasoning is specific to skills. A declared MCP server is a tenant of the same
run environment, it sits in the context window on every attempt whether or not
the agent ever calls it, and whether it earns that context is the question
ablation already answers for skills. So `--ablate` now resolves against the union
of the spec's `skills:` and its `mcp:` servers, and an ablated server is left out
of the harness config for that run — the agent never sees its tool definitions.
No spec change: subjecthood stays a runtime axis, chosen per invocation and
recorded in `RunMeta`.

## A collision is refused, not guessed

A skill and an `mcp:` server can share a name. The bare name is then refused, and
the caller says which kind they meant with a qualifier: `--ablate mcp:weather`
removes the server, `--ablate skill:weather` removes the skill. Neither a skill's
frontmatter `name:` nor an `mcp:` key may contain `:`, so a qualified entry can
never collide with a declared name, and what you type is what the run records.

Refusing is the same stance `apply_ablation` already takes on an undeclared name:
a silently-wrong ablation removes the wrong tenant and still produces a plausible
number with nothing in the output to invite suspicion. "Always prefer the skill"
would be that failure with a rule attached.

The `skill:` qualifier is added alongside `mcp:` for symmetry. A collision that
can only name one of its two sides is not actually resolvable, and the caller who
wants the skill removed is the one who would be stuck.

## A server ablation leaves the activation expectation alone

[0015](0015-ablation-names-its-subject-at-the-invocation.md) drops every task's
`activates:` expectation on an ablated run, because filtering it would assert a
claim the author never wrote. That rule is about removed **skills**: `activates:`
names skills, and removing a server leaves every one of them installed and
observable, so withholding the verdict would drop a measurement nothing changed.
`expected_activation` therefore keys on the removed-skill subset, not on whether
the marker is non-empty.

Nothing structural asserts on an `mcp__<server>__<tool>` handle today. A
transcript's namespaced call is checked only by a free-text `expect:` judge or an
`assert:` script, neither of which caliper can reinterpret on the caller's
behalf, so there is no server-side analogue of `activates:` to drop.

## What a run records, and what `compare` checks

`RunMeta.ablated` is one list, unchanged in shape: a removed skill stays bare and
a removed server carries the `mcp:` qualifier, so existing saved runs parse
unchanged. `RunMeta.ablated_skills` and `ablated_servers` split it for readers
rather than making each of them re-parse the qualifier.

`RunMeta.mcp_servers` records the servers the run was actually configured with,
after ablation — the server half of what `skill_snapshots` is for skills. The
marker says what was taken out, the membership says what was left, and together
they let a saved run describe its own environment.

`compare` labels an ablation pair only when the marker agrees with that record:
every removed skill must be present in the full side's snapshots and absent from
the ablated side's, and every removed server present in the full side's
`mcp_servers` and absent from the ablated side's. Without the membership a spec
that dropped the server between two runs would be misread as an ablation of it —
both sides ran without it, yet the marker would claim the difference. Server
*configuration* drift (a `weather` pointing somewhere else between runs) is not
tracked; that is a separate comparison concern, the way skill text drift is.

A run saved before `mcp_servers` existed loads with the field `None`, which means
"not recorded" rather than "recorded as none". The server check stands down when
either side of a pair is `None`, so an older run still pairs with a new ablated
run and gets its labels; a record of `[]` is a real "no servers" and is checked.

"Bare agent" is the label only when nothing was configured at all: no skills,
and a *recorded* set of no servers. A run that kept a server, or whose membership
was never recorded, reads `without <subjects>` — "bare agent" would claim a fact
the record does not support.

Two runs whose recorded memberships differ outside a recognised ablation pair get
the warning the skill axis already gets (`different MCP servers configured`),
since tool availability can move the score for reasons unrelated to the skill.

The qualifier is also what `report` reads to tell the two report shapes apart: a
removed skill renders "activation observed, not scored" and the observed-only
table, while a removed server keeps the scored activation table because the
verdicts are still real.

## An empty declared set still isolates

> Superseded in part by [0026](0026-attempts-never-see-account-connectors.md):
> no `mcp:` block now isolates to zero servers too.

`RunContext.mcp_servers` distinguishes "no `mcp:` block" (`None`, so the CLI's own
ambient config applies, as it always has) from "a declared block whose servers
were all ablated" (an empty mapping). An authored `mcp: {}` is that same
declared-and-empty case: `runner.py` asks the spec for field *presence*, not
truthiness, so an explicitly empty block still isolates. Claude Code writes its
config and passes `--strict-mcp-config` for both declared cases, so an
all-ablated run sees zero servers rather than the ones the seeded user config
carries; hermes and codex already overwrite the seeded MCP section wholesale, so
an empty declared set normalizes to their zero-server shape either way.

## Backends without MCP

`runner.py` refuses a spec that declares `mcp:` on a backend where `supports_mcp`
is False, because the declared tools would otherwise be silently absent and every
attempt would test something other than what the spec claims. Ablation resolves
before that guard, so the guard now sees the **surviving** servers: a spec whose
servers were all ablated is runnable on such a backend, because the absence is the
caller's explicit choice and the marker records it. A surviving server still
refuses, unchanged.

## Considered options

- **A separate `--ablate-mcp <name>` flag.** Rejected: two flags for one
  question, and a name that is unambiguous has no reason to make the caller know
  which kind declared it. The qualifier carries that only when it is needed.
- **Always preferring the skill on a collision.** Rejected above; it is the
  silent-wrong-ablation failure mode with a rule attached.
- **Preferring the server on a collision.** Same failure, mirrored, and it would
  quietly change what an existing `--ablate <skill>` invocation removes.
- **A structured MCP assertion to go with `activates:`.** Rejected as a
  different feature with its own design surface; nothing in the repo asserts on a
  server handle structurally, so there is nothing for ablation to drop yet.
- **Trusting an `mcp:` marker in `compare` without recording membership.**
  Rejected: it is the one thing
  [0015](0015-ablation-names-its-subject-at-the-invocation.md) refused to do for
  skills, and a stale spec is exactly the case the check exists to catch.

## Out of scope

Ablating a custom rule (`CLAUDE.md`, `AGENTS.md`). Same question, but the
mechanism is different enough to deserve its own record.

Server configuration drift — a `weather` that points somewhere else between runs
— is not detected. Membership is what ablation attribution needs; drift is a
comparison feature of its own, as skill text drift was for skills.

## Consequences

- `Ablation` (`caliper/skills.py`) becomes the one place that resolves
  `--ablate`, and it now returns the surviving skills, the surviving servers, the
  recorded marker, and the removed-skill subset rather than a bare list of refs.
- The MCP-unsupported guard moves after skill resolution and ablation, since it
  needs the surviving set. A spec with a bad skill entry now reports the skill
  error first; both are still refusals before any paid attempt.
- `RunMeta` gains `mcp_servers`, an additive field that is `None` on runs saved
  before it, so an old run's membership reads as unknown rather than empty.
- `compare` grows an `mcp_mismatch` warning beside `neighbourhood_mismatch`, and
  its marker check tightens for skills too: a removed skill must now be present
  on the full side, not merely absent from the ablated one. A pair whose spec
  dropped the member between runs stops being labelled, which is the point.
- `AblationError`, a `SkillResolutionError` subclass, gives the run panel
  "Invalid ablation" without every raise site passing a title.
- A bare `--ablate <name>` that used to name a skill starts failing if the spec
  later adds a server of that name. That is the intended reading of "refuse
  rather than guess" — the invocation stops rather than silently switching
  subjects — and `--ablate skill:<name>` restores it.
