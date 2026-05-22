---
name: evaluate-skill
description: Evaluate Claude Code, Codex, or API-backed skills with the caliper CLI using repeatable tasks, automated judging, and pass@k scoring.
allowed-tools: Bash
---

# Evaluate Skill

Use this skill to evaluate Claude Code, Codex, or API-backed skills using the `caliper` CLI.
Caliper runs repeatable task specs against an agent backend, judges the results
with an LLM and/or deterministic Python assertions, and reports pass@k scores.

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
