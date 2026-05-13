---
description: Run skill evaluations with verdict — evaluate Claude Code skills using automated judging and pass@k scoring
allowed-tools: Bash
---

# verdict — skill evaluator

Use this skill to evaluate a Claude Code skill using the `verdict` CLI.

## Commands

### Run an evaluation
```bash
verdict run path/to/spec.eval.yaml --k 3
verdict run path/to/spec.eval.yaml --k 3 --baseline      # include no-skill delta
verdict run path/to/spec.eval.yaml --judge script        # LLM writes assertion scripts
verdict run path/to/spec.eval.yaml --verbose             # show per-attempt reasoning
```

### Create a new evaluation spec (interactive wizard)
```bash
verdict new my-skill-eval
verdict new --skill ~/.claude/commands/review.md --backend claude
```

### Validate a spec file
```bash
verdict validate path/to/spec.eval.yaml
```

### Browse saved results
```bash
verdict list                        # all specs with latest scores
verdict list my-skill-eval          # all runs for one spec
verdict report my-skill-eval        # latest run (table view)
verdict report my-skill-eval --run 2026-05-12T14-23-01Z  # specific run
verdict report results.json --format json
```

## Spec format (.eval.yaml)

```yaml
skill:
  path: ~/.claude/commands/my-skill.md
  backend: claude          # claude | codex
  model: claude-sonnet-4-6 # optional

judge:
  backend: claude
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

## Key concepts

- **pass@k** — probability that at least 1 of k attempts passes (default k=3)
- **baseline** — runs each task without the skill to compute a delta score
- **autorater** — LLM reads the full tool-call transcript + expectation → pass/fail
- **script judge** — LLM can write Python assertion scripts for verifiable facts
- **cheat detection** — transcript is scanned for reads of forbidden files (spec, results)
- **isolation** — each attempt runs in a fresh temp HOME with no session history

## Results storage

Results are saved automatically to `.verdict/results/<spec-name>/<timestamp>.json`
alongside the spec file. Each result includes a full skill snapshot (content + git SHA
of the skill file and any referenced scripts) for reproducibility.
