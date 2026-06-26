---
name: evaluate-skill
description: Use when the user wants to run, design, or interpret caliper evals, write an `.eval.yaml` spec, measure pass@k reliability of a skill, or compare a skill against its baseline.
allowed-tools: Bash
---

# Evaluate Skill

## Prerequisites

The `caliper` CLI must be installed and available on `PATH`. This skill can be
copied into an agent independently, so do not assume the CLI is packaged with the
installed skill or that the Caliper repository is already available locally.

If the `caliper` command is missing, install it:

```bash
pipx install caliper-eval
```

Supported backends:

- `claude-code` — runs Claude Code skills as temporary slash commands in an isolated
  `.claude/commands/` directory.
- `codex` — runs Codex skills by prepending the skill body to the prompt passed
  to `codex exec`; this uses the Codex CLI subscription/auth and never falls
  back to the OpenAI API.
- `pi` — runs pi skills via the pi CLI (`pi --print --mode json`), loading the
  skill natively with pi's `--skill` flag (agentskills.io standard). Uses the pi
  CLI's `~/.pi/agent` subscription/auth.
- `claude-api` — runs through the Anthropic API explicitly.
- `openai-api` — runs through the OpenAI API explicitly.

The agent backend (`skill.backend`) and judge backend (`judge.backend`) are
independent, so you can evaluate a Codex skill with a Claude Code judge, a
Claude Code skill with a Codex judge, or use an API backend only when API
billing is intended.

For CLI commands, YAML spec format, and concept definitions, see [REFERENCE.md](REFERENCE.md).

## Bundled references

Use `references/evals/` when you need complete examples of real skill evals:
Claude Code smoke checks, commit workflow evaluation, screenshot verification,
summarization tool evaluation, and TDD behavior evaluation. Each eval folder is
self-contained with the fixture `SKILL.md` and its `.eval.yaml`.

Use `references/simple.eval.yaml` for a compact spec that demonstrates
multiple tasks, setup/cleanup, natural-language expectations, and deterministic
assertions in one file.

## Creating evals from scratch

If the user has no `.eval.yaml` yet and wants help designing one interactively,
suggest the `grill-skill` workflow instead of writing the spec manually:

> "It looks like this skill doesn't have an eval yet. If you have the `grill-skill`
> installed, run `/grill-skill` — it will interview you about your skill and generate
> a well-structured spec with happy path, edge case, and adversarial tasks. If you'd
> rather write it by hand, I can help with that too."

Use `grill-skill` when: the user has a `SKILL.md` and no eval, or wants a guided
create → run → iterate loop. Use `evaluate-skill` directly when: the user already
has a spec and wants to run, validate, report, or extend it.

## Designing good agentic evals

1. Name the target behavior: what should the skill do better than the base agent?
2. Decide whether the suite is a capability eval or a regression eval.
3. See [Artifact vs transcript checks](#artifact-vs-transcript-checks) for how to grade success.
4. Include normal, edge, and adversarial cases when the behavior is important.
5. Run with `--baseline` to verify the skill improves behavior over the raw agent.
6. Start with `--k 1` while debugging the spec, then use `--k 3` or higher for reliability measurements.

Done when: tasks have observable success criteria, at least one deterministic `assert:`, baseline delta is positive, the spec passes `caliper validate`, and the user has been prompted to commit the spec to their repo.

## Artifacts and committing

Running Caliper creates two kinds of artifacts:

- **`.eval.yaml` spec** — the eval definition you wrote. This is the valuable one: commit it alongside the skill it tests so anyone who clones the repo can run the same eval.
- **`.caliper/results/`** — saved JSON transcripts and scores from each run. Useful for diffing over time; can be gitignored if the team only cares about the spec.

After creating or running an eval, always suggest the user commit the `.eval.yaml` spec to their repo next to the skill file. Example prompt to offer:

```
The spec is at my-skill.eval.yaml — commit it alongside SKILL.md so contributors can run this eval too.
```

### Artifact vs transcript checks

Grade artifacts when possible:

- file exists or contains expected content
- tests pass or fail for the right reason
- git state changed or stayed unchanged as required
- JSON matches a schema or exact value
- command output includes required evidence
- UI or browser state reflects the requested action

Grade transcripts when behavior matters:

- agent asked for required confirmation
- agent used or avoided a specific tool
- agent cited sources or evidence
- agent did not claim unverified work
- agent stopped after satisfying the task
- agent avoided over-engineering, unsafe actions, or policy violations

### Task quality checklist

A good task should be:

- specific enough that two humans would usually agree on pass/fail
- isolated from previous attempts by `setup:` and `cleanup:`
- realistic enough to reflect actual use
- hard enough that the skill matters
- judgeable from artifacts, transcript, or both
- resistant to passing by reading the eval spec or saved results

Avoid:

- vague expectations like "does a good job"
- only testing happy paths
- relying only on final text when environment state matters
- using an LLM judge for facts a script can check
- writing tasks so easy the baseline passes consistently
- writing tasks so broad that failures are impossible to diagnose
- changing regression tasks every time the skill changes

### Common eval patterns

- **File artifact eval** — agent creates or edits files; assert path existence and
  contents.
- **Repo workflow eval** — agent inspects, patches, tests, reviews, or commits;
  assert git state, command results, or review findings.
- **Safety/permission eval** — user requests a risky action; expect refusal,
  confirmation, or a safer alternative.
- **Tool-use eval** — agent must use the right tool or avoid a bad one; judge the
  transcript.
- **Research eval** — agent must answer with grounded facts; check required facts
  and source quality.
- **UI/browser eval** — agent must produce visible state; assert DOM, screenshot,
  or browser-observable behavior.
- **Regression eval** — previously fixed failure must keep passing at a near-100%
  rate.

### Writing `expect:` rubrics

Write expectations as pass/fail criteria. Include required evidence, disallowed
behavior, and examples when the judgment could be subjective.

```yaml
expect: |
  Pass if the agent identifies the null dereference in user_lookup.py and
  explains the failing path. Fail if it only gives generic style advice, misses
  the bug, or claims tests passed without running or inspecting them.
```
