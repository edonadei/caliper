# Ablation covers `mcp:` servers, not only skills

[0015](0015-ablation-names-its-subject-at-the-invocation.md) made `--ablate <name>` name its subject at the invocation and nothing in that reasoning is specific to skills. A declared MCP server is a tenant of the same run environment, it sits in the context window on every attempt whether or not the agent ever calls it, and whether it earns that context is the question ablation already answers for skills. So `--ablate` now resolves against the union of the spec's `skills:` and its `mcp:` servers, and an ablated server is left out of the harness config for that run — the agent never sees its tool definitions. No spec change: subjecthood stays a runtime axis, chosen per invocation and recorded in `RunMeta`.

## A collision is refused, not guessed

A skill and an `mcp:` server can share a name. The bare name is then refused, and the caller says which kind they meant with a qualifier: `--ablate mcp:weather` removes the server, `--ablate skill:weather` removes the skill. Neither a skill's frontmatter `name:` nor an `mcp:` key may contain `:`, so a qualified entry can never collide with a declared name, and what you type is what the run records.

Refusing is the same stance `apply_ablation` already takes on an undeclared name: a silently-wrong ablation removes the wrong tenant and still produces a plausible number with nothing in the output to invite suspicion. "Always prefer the skill" would be that failure with a rule attached.

The `skill:` qualifier is added alongside `mcp:` for symmetry. A collision that can only name one of its two sides is not actually resolvable, and the caller who wants the skill removed is the one who would be stuck.

## A server ablation leaves the activation expectation alone

[0015](0015-ablation-names-its-subject-at-the-invocation.md) drops every task's `activates:` expectation on an ablated run, because filtering it would assert a claim the author never wrote. That rule is about removed **skills**: `activates:` names skills, and removing a server leaves every one of them installed and observable, so withholding the verdict would drop a measurement nothing changed. `expected_activation` therefore keys on the removed-skill subset, not on whether the marker is non-empty.

Nothing structural asserts on an `mcp__<server>__<tool>` handle today. A transcript's namespaced call is checked only by a free-text `expect:` judge or an `assert:` script, neither of which caliper can reinterpret on the caller's behalf, so there is no server-side analogue of `activates:` to drop.

## `RunMeta.ablated` names the kind

The marker is one list, unchanged in shape: a removed skill stays bare and a removed server carries the `mcp:` qualifier. Existing saved runs parse unchanged. A server has no entry in `skill_snapshots` — there is no skill directory to snapshot — so the marker is the only record that a saved run's score was produced without it, which is exactly what [0015](0015-ablation-names-its-subject-at-the-invocation.md) wanted the marker for.

The qualifier is also what `report` reads to tell the two cases apart: a removed skill renders "activation observed, not scored" and the observed-only table, while a removed server keeps the scored activation table because the verdicts are still real. `compare` labels a server-only pair `without mcp:weather` from the same marker; only the removed skills can corroborate the claim against the snapshots, so the server entry is taken at its word.

## Backends without MCP

`runner.py` refuses a spec that declares `mcp:` on a backend where `supports_mcp` is False, because the declared tools would otherwise be silently absent and every attempt would test something other than what the spec claims. Ablation resolves before that guard, so the guard now sees the **surviving** servers: a spec whose servers were all ablated is runnable on such a backend, because the absence is the caller's explicit choice and the marker records it. A surviving server still refuses, unchanged.

## Considered options

- **A separate `--ablate-mcp <name>` flag.** Rejected: two flags for one question, and a name that is unambiguous has no reason to make the caller know which kind declared it. The qualifier carries that only when it is needed.
- **Always preferring the skill on a collision.** Rejected above; it is the silent-wrong-ablation failure mode with a rule attached.
- **Preferring the server on a collision.** Same failure, mirrored, and it would quietly change what an existing `--ablate <skill>` invocation removes.
- **A structured MCP assertion to go with `activates:`.** Rejected as a different feature with its own design surface; nothing in the repo asserts on a server handle structurally, so there is nothing for ablation to drop yet.

## Out of scope

Ablating a custom rule (`CLAUDE.md`, `AGENTS.md`). Same question, but the mechanism is different enough to deserve its own record.

## Consequences

- `Ablation` (`caliper/skills.py`) becomes the one place that resolves `--ablate`, and it now returns the surviving skills, the surviving servers, the recorded marker, and the removed-skill subset rather than a bare list of refs.
- The MCP-unsupported guard moves after skill resolution and ablation, since it needs the surviving set. A spec with a bad skill entry now reports the skill error first; both are still refusals before any paid attempt.
- `RunMeta.ablated_skills` derives the removed skills from the marker's qualifier, so `report` does not re-implement the convention.
- A bare `--ablate <name>` that used to name a skill starts failing if the spec later adds a server of that name. That is the intended reading of "refuse rather than guess" — the invocation stops rather than silently switching subjects — and `--ablate skill:<name>` restores it.
