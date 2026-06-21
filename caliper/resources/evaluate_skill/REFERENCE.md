# Caliper Reference

## Commands

### Run an evaluation
```bash
caliper run path/to/spec.eval.yaml --k 3
caliper run path/to/spec.eval.yaml --k 3 --baseline      # include no-skill delta
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
caliper report my-skill-eval        # latest run — failed tasks shown with detail
caliper report my-skill-eval --verbose  # all tasks with full output
caliper report my-skill-eval --run 2026-05-12T14-23-01Z  # specific run
caliper report results.json --format json
```

After any `caliper run`, failed tasks are automatically shown with their agent
output (last 500 chars) and `assert_evidence`. Use `--verbose` when you need
full output for all tasks including passing ones.

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
- **judge** — the spec drives evaluation: `expect:` triggers an LLM verdict (which may generate a Python assertion script); `assert:` runs a deterministic Python script; both can be combined and both must pass
- **cheat detection** — transcript is scanned for reads of forbidden files (spec, results)
- **isolation** — each attempt runs in a fresh temp HOME with no session history

## Results storage

Results are saved automatically to `.caliper/results/<spec-name>/<timestamp>.json`
alongside the spec file. Each result includes a full skill snapshot (content + git SHA
of the skill file and any referenced scripts) for reproducibility.
