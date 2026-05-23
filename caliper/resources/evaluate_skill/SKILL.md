---
name: evaluate-skill
description: Evaluate Claude Code, Codex, or API-backed skills with the caliper CLI using repeatable tasks, automated judging, and pass@k scoring.
allowed-tools: Bash
---

# Evaluate Skill

Use this skill to evaluate Claude Code, Codex, or API-backed skills using the `caliper` CLI.
Caliper runs repeatable task specs against an agent backend, judges the results
with an LLM and/or deterministic Python assertions, and reports pass@k scores.

## Prerequisites

The `caliper` CLI must be installed and available on `PATH`. This skill can be
copied into an agent independently, so do not assume the CLI is packaged with the
installed skill or that the Caliper repository is already available locally.

If the `caliper` command is missing, install it from the Caliper CLI repository:
`https://github.com/edonadei/verdict`.

From the Caliper repository root:

```bash
pip install -e .
```

Supported backends:

- `claude-code` — runs Claude Code skills as temporary slash commands in an isolated
  `.claude/commands/` directory.
- `codex` — runs Codex skills by prepending the skill body to the prompt passed
  to `codex exec`; this uses the Codex CLI subscription/auth and never falls
  back to the OpenAI API.
- `claude-api` — runs through the Anthropic API explicitly.
- `openai-api` — runs through the OpenAI API explicitly.

The agent backend (`skill.backend`) and judge backend (`judge.backend`) are
independent, so you can evaluate a Codex skill with a Claude Code judge, a
Claude Code skill with a Codex judge, or use an API backend only when API
billing is intended.

## Commands

### Run an evaluation
```bash
caliper run path/to/spec.eval.yaml --k 3
caliper run path/to/spec.eval.yaml --k 3 --baseline      # include no-skill delta
caliper run path/to/spec.eval.yaml --judge script        # LLM writes assertion scripts
caliper run path/to/spec.eval.yaml --verbose             # show per-attempt reasoning
```

### Create a new evaluation spec (interactive wizard)
```bash
caliper new my-skill-eval
caliper new --skill ~/.claude/commands/review.md --backend claude-code
caliper new --skill ./SKILL.md --backend codex
caliper new --skill ./SKILL.md --backend openai-api
```

### Validate a spec file
```bash
caliper validate path/to/spec.eval.yaml
```

### Browse saved results
```bash
caliper list                        # all specs with latest scores
caliper list my-skill-eval          # all runs for one spec
caliper report my-skill-eval        # latest run (table view)
caliper report my-skill-eval --run 2026-05-12T14-23-01Z  # specific run
caliper report results.json --format json
```

## Bundled references

Use `references/evals/` when you need complete examples of real skill evals:
Claude Code smoke checks, commit workflow evaluation, screenshot verification,
summarization tool evaluation, and TDD behavior evaluation. Each eval folder is
self-contained with the fixture `SKILL.md` and its `.eval.yaml`.

Use `references/examples/simple.eval.yaml` for a compact spec that demonstrates
multiple tasks, setup/cleanup, natural-language expectations, and deterministic
assertions in one file.

## Designing good agentic evals

Before writing YAML, first define the behavior the eval should protect or
improve. A good agentic eval makes success observable, repeatable, and hard to
game.

Use this workflow when asked to create or improve an eval:

1. Name the target behavior: what should the skill do better than the base agent?
2. Decide whether the suite is a capability eval or a regression eval.
3. Define success as observable state, not assistant intent.
4. Prefer deterministic `assert:` checks for files, commands, repo state, JSON,
   UI state, API results, or other facts the harness can verify.
5. Use `expect:` for judgment that cannot be made deterministically, and write it
   as clear pass/fail criteria.
6. Include normal, edge, and adversarial cases when the behavior is important.
7. Run with `--baseline` to verify the skill improves behavior over the raw
   agent.
8. Start with `--k 1` while debugging the spec, then use `--k 3` or higher for
   reliability measurements.

### Outcome vs transcript checks

Grade outcomes when possible:

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

## Spec format (.eval.yaml)

```yaml
skill:
  path: ./SKILL.md
  backend: claude-code     # claude-code | codex | claude-api | openai-api
  model: claude-sonnet-4-6 # optional

judge:
  backend: claude-code
  model: claude-haiku-4-5-20251001  # optional; cheaper is fine for judging

sandbox:
  forbidden_files:
    - ".*\\.eval\\.yaml$"   # agent cannot read the spec

tasks:
  - id: task-001
    name: Short description of what success looks like
    setup: <shell command to prepare the environment>    # optional
    cleanup: <shell command to tear down>               # optional
    prompt: <prompt sent to the AI under evaluation>
    expect: <natural language description of a successful outcome>

  - id: task-002
    name: Task with a deterministic assertion
    prompt: Write hello to /tmp/out.txt
    expect: A file is created at /tmp/out.txt
    assert: |
      import os
      assert os.path.exists("/tmp/out.txt"), "File not created"

  - id: task-003
    name: Task with external assertion script
    prompt: Generate a report
    assert: ./assertions/check_report.py
```

For a Codex-backed eval, use:

```yaml
skill:
  path: ./SKILL.md
  backend: codex

judge:
  backend: codex
```

For an API-backed eval, opt in explicitly:

```yaml
skill:
  path: ./SKILL.md
  backend: openai-api
  model: gpt-4o-mini

judge:
  backend: openai-api
  model: gpt-4o-mini
```

## Key concepts

- **pass@k** — probability that at least 1 of k attempts passes (default k=3)
- **baseline** — runs each task without the skill to compute a delta score
- **autorater** — LLM reads the full tool-call transcript + expectation → pass/fail
- **script judge** — LLM can write Python assertion scripts for verifiable facts
- **cheat detection** — transcript is scanned for reads of forbidden files (spec, results)
- **isolation** — each attempt runs in a fresh temp HOME with no session history

## Results storage

Results are saved automatically to `.caliper/results/<spec-name>/<timestamp>.json`
alongside the spec file. Each result includes a full skill snapshot (content + git SHA
of the skill file and any referenced scripts) for reproducibility.
