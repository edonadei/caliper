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
5. Run with `--baseline` to confirm the skill beats the raw agent. Debug the spec at `--k 1`, then measure reliability at `--k 3` or higher.

**Done when:** tasks have observable success criteria, at least one deterministic `assert:`, a positive baseline delta, the spec passes `caliper validate`, and the user has been prompted to commit the spec.

## Committing

Running Caliper produces two artifacts: the `.eval.yaml` spec — the valuable one, commit it beside the skill so anyone who clones the repo can run the same eval — and `.caliper/results/` saved run JSONs, useful for diffing over time and safe to gitignore. After creating or running an eval, tell the user to commit the spec alongside `SKILL.md`.
