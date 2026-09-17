# Ablation covers `mcp:` servers, not only skills

[0015](0015-ablation-names-its-subject-at-the-invocation.md) made `--ablate <name>` name its subject at the invocation and nothing in that reasoning is specific to skills. A declared MCP server is a tenant of the same run environment, it sits in the context window on every attempt whether or not the agent ever calls it, and whether it earns that context is the question ablation already answers for skills. So `--ablate` now resolves against the union of the spec's `skills:` and its `mcp:` servers, and an ablated server is left out of the harness config for that run — the agent never sees its tool definitions. No spec change: subjecthood stays a runtime axis, chosen per invocation and recorded in `RunMeta`.

## A collision is refused, not guessed

A skill and an `mcp:` server can share a name. The bare name is then refused, and the caller says which kind they meant with a qualifier: `--ablate mcp:weather` removes the server, `--ablate skill:weather` removes the skill. Neither a skill's frontmatter `name:` nor an `mcp:` key may contain `:`, so a qualified entry can never collide with a declared name, and what you type is what the run records.

Refusing is the same stance `apply_ablation` already takes on an undeclared name: a silently-wrong ablation removes the wrong tenant and still produces a plausible number with nothing in the output to invite suspicion. "Always prefer the skill" would be that failure with a rule attached.

The `skill:` qualifier is added alongside `mcp:` for symmetry. A collision that can only name one of its two sides is not actually resolvable, and the caller who wants the skill removed is the one who would be stuck.

## A server ablation leaves the activation expectation alone

[0015](0015-ablation-names-its-subject-at-the-invocation.md) drops every task's `activates:` expectation on an ablated run, because filtering it would assert a claim the author never wrote. That rule is about removed **skills**: `activates:` names skills, and removing a server leaves every one of them installed and observable, so withholding the verdict would drop a measurement nothing changed. `expected_activation` therefore keys on the removed-skill subset, not on whether the marker is non-empty.

Nothing structural asserts on an `mcp__<server>__<tool>` handle today. A transcript's namespaced call is checked only by a free-text `expect:` judge or an `assert:` script, neither of which caliper can reinterpret on the caller's behalf, so there is no server-side analogue of `activates:` to drop.

## `RunMeta` records the marker and the membership

`RunMeta.ablated` is one list, unchanged in shape: a removed skill stays bare and a removed server carries the `mcp:` qualifier, so existing saved runs parse unchanged. Alongside it, `RunMeta.mcp_servers` records the servers the run was actually configured with — after ablation — which is the server half of what `skill_snapshots` is for skills. Together they are what makes a saved run describe its own environment: the marker says what was taken out, the membership says what was left.

The membership is also what keeps `compare` honest. [0015](0015-ablation-names-its-subject-at-the-invocation.md) labels an ablation pair only when the marker agrees with the run's own records, and a server has no skill snapshot to agree with. Without a record of the full side's servers the marker would have to be taken at its word, and a spec that dropped the server between two runs would be misread as an ablation of it — both sides ran without the server, yet the marker would claim the difference. `compare` now requires every removed server to be present in the full side's `mcp_servers` and absent from the ablated side's, exactly as it requires a removed skill of the snapshots. Server *configuration* drift — the same name pointing at a different command or url — is not tracked; that is a separate concern, the way skill text drift is a separate one from skill membership.

The qualifier is also what `report` reads to tell the two report shapes apart: a removed skill renders "activation observed, not scored" and the observed-only table, while a removed server keeps the scored activation table because the verdicts are still real.

## An empty declared set still isolates

`RunContext.mcp_servers` distinguishes "no `mcp:` block" (`None`, so the CLI's own ambient config applies, as it always has) from "a declared block whose servers were all ablated" (an empty mapping). Claude Code writes its config and passes `--strict-mcp-config` for both declared cases, so an all-ablated run sees zero servers rather than the ones the seeded user config carries; hermes and codex already overwrite the seeded MCP section wholesale, so an empty declared set normalizes to their zero-server shape either way.

## Backends without MCP

`runner.py` refuses a spec that declares `mcp:` on a backend where `supports_mcp` is False, because the declared tools would otherwise be silently absent and every attempt would test something other than what the spec claims. Ablation resolves before that guard, so the guard now sees the **surviving** servers: a spec whose servers were all ablated is runnable on such a backend, because the absence is the caller's explicit choice and the marker records it. A surviving server still refuses, unchanged.

## Considered options

- **A separate `--ablate-mcp <name>` flag.** Rejected: two flags for one question, and a name that is unambiguous has no reason to make the caller know which kind declared it. The qualifier carries that only when it is needed.
- **Always preferring the skill on a collision.** Rejected above; it is the silent-wrong-ablation failure mode with a rule attached.
- **Preferring the server on a collision.** Same failure, mirrored, and it would quietly change what an existing `--ablate <skill>` invocation removes.
- **A structured MCP assertion to go with `activates:`.** Rejected as a different feature with its own design surface; nothing in the repo asserts on a server handle structurally, so there is nothing for ablation to drop yet.
- **Trusting an `mcp:` marker in `compare` without recording membership.** Rejected: it is the one thing [0015](0015-ablation-names-its-subject-at-the-invocation.md) refused to do for skills, and a stale spec is exactly the case the check exists to catch.

## Out of scope

Ablating a custom rule (`CLAUDE.md`, `AGENTS.md`). Same question, but the mechanism is different enough to deserve its own record.

Server configuration drift (a `weather` that points somewhere else between runs) is not detected. Membership is what ablation attribution needs; drift is a comparison feature of its own, as skill text drift was for skills.

## Consequences

- `Ablation` (`caliper/skills.py`) becomes the one place that resolves `--ablate`, and it now returns the surviving skills, the surviving servers, the recorded marker, and the removed-skill subset rather than a bare list of refs.
- The MCP-unsupported guard moves after skill resolution and ablation, since it needs the surviving set. A spec with a bad skill entry now reports the skill error first; both are still refusals before any paid attempt.
- `RunMeta` gains `mcp_servers`, an additive field, so saved runs from before it parse with an empty membership and a skill-only pair is unaffected.
- `compare`'s marker check tightens for skills too: a removed skill must now be present on the full side, not merely absent from the ablated one. A pair whose spec dropped the member between runs stops being labelled, which is the point.
- `RunMeta.ablated_skills` derives the removed skills from the marker's qualifier, so `report` does not re-implement the convention.
- A bare `--ablate <name>` that used to name a skill starts failing if the spec later adds a server of that name. That is the intended reading of "refuse rather than guess" — the invocation stops rather than silently switching subjects — and `--ablate skill:<name>` restores it.
