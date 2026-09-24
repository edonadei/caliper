---
name: evaluate-skill
description: Measure a skill's reliability — run it k times for a pass@k score, design or interpret its eval, or compare it against the base agent. Use when the user wants to run, design, or interpret a skill's eval, or write an .eval.yaml spec.
allowed-tools: Bash
---

# Evaluate Skill

Run a skill repeatedly to measure how reliably it works, and design the evals that measure it.

## Prerequisites

The `caliper` CLI must be on `PATH`. This skill can be copied into an agent without the Caliper repo, so do not assume the CLI is packaged with it. Install if missing:

```bash
pipx install caliper-eval
```

The engine (backend + model) is not part of the spec — it is chosen at run time with `--model` (skill) and `--judge-model` (judge), independently, from `claude-code`, `codex`, `pi`, defaulting to `claude-code`. Every backend is a CLI agent that uses its own subscription/auth; there is no direct-API backend (for API billing, configure a CLI with an API key). Full per-backend detail and every command: [REFERENCE.md](REFERENCE.md).

## Spec shape

An `.eval.yaml` names the skill and a list of tasks. Keep `skill.path` relative to the spec file (usually `./SKILL.md`):

```yaml
skill:
  path: ./SKILL.md      # relative to the spec file
tasks:
  - name: What success looks like
    prompt: <prompt sent to the skill under test>
    expect: <natural-language pass/fail criterion>
    assert: |           # optional deterministic Python check
      assert ...
```

The spec has no `backend`/`model` or `judge:` block; pick the engine when you run, e.g. `caliper run <spec> --model codex --judge-model codex`. The full format (setup/cleanup, external assert scripts, sandbox) is in [REFERENCE.md](REFERENCE.md).

## Bundled references

`references/evals/` holds complete real examples (Claude Code smoke, commit workflow, screenshot, summarization, TDD) — each folder self-contained with its fixture `SKILL.md` and `.eval.yaml`. `references/simple.eval.yaml` is one compact multi-task spec.

## No eval yet?

If the skill has a `SKILL.md` but no `.eval.yaml`, suggest the `grill-skill` workflow — it interviews the user and generates a happy/edge/adversarial spec. Use `evaluate-skill` directly when a spec already exists and the user wants to run, validate, report, or extend it.

## Designing good evals

1. Name the target behavior — what should the skill do better than the base agent?
2. Decide whether the suite is a capability eval or a regression eval.
3. Cover normal, edge, and adversarial cases when the behavior matters.
4. Grade artifacts (files, git state, command output, exact values) whenever you can; judge the transcript only when the behavior itself is the point. The full artifact-vs-transcript rules, the task-quality checklist, common eval patterns, and how to write `expect:` rubrics live in [REFERENCE.md](REFERENCE.md) — read and apply them when designing tasks.
5. Run once with `--ablate <skill-name>` and `caliper compare` the two runs, to confirm the skill beats the raw agent. Debug the spec at `--k 1`, then measure reliability at `--k 3` or higher.

**Done when:** tasks have observable success criteria, at least one deterministic `assert:`, a positive delta against the ablated run, the spec passes `caliper validate`, and the user has been prompted to commit the spec.

## Whose setup is measured

By default every run **loads the user's own customizations**: the servers their CLI is configured with and their account's hosted connectors, merged with the spec's `mcp:`. That answers "does my skill work in *my* agent?", which is what most users want. Isolating a run (`--no-user-customizations`, or `user_customizations: false` in the spec) gives the attempt only the declared servers, so the score is portable.

**Keep the default** when:
- the user is testing or iterating on their own skill in their own agent;
- ablating one of the spec's skills or servers (`--ablate`): both runs load the same setup, so the difference is still the removed subject.

**Isolate** when:
1. comparing backends or models (`--model claude-code` vs `--model codex`): each CLI loads a different setup, so the delta would partly be the setups;
2. the number leaves this machine: shared, published, in a README, or compared with someone else's run;
3. measuring the bare agent, or checking that an attempt sees only the declared servers.

**Always tell the user which mode the run used** and what it loaded: read the report header's `user customizations:` line (absent means isolated). A score from their setup and a portable score look identical otherwise. If `caliper compare` warns about user customizations, relay its suggested fix rather than the delta alone.

## Committing

Running Caliper produces two artifacts: the `.eval.yaml` spec — the valuable one, commit it beside the skill so anyone who clones the repo can run the same eval — and `.caliper/results/` saved run JSONs, useful for diffing over time and safe to gitignore. After creating or running an eval, tell the user to commit the spec alongside `SKILL.md`.
