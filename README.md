# verdict

Evaluate AI skills with confidence. `verdict` runs your Claude Code skills (or Codex) against a set of tasks, judges each attempt using an LLM autorater or assertion scripts, and scores results with **pass@k**.

```
╭──────────────────────────────────────────────────────╮
│  ⚖  VERDICT  ·  code-review-eval  ·  k=3  ·  claude  │
╰──────────────────────────────────────────────────────╯

 ┌──────────┬──────────────────────────┬─────┬─────────┬────────────┐
 │ ID       │ Task                     │ k   │ pass@k  │            │
 ├──────────┼──────────────────────────┼─────┼─────────┼────────────┤
 │ task-001 │ Detects a null ptr bug   │ 3/3 │ 100.0%  │ ✓ PASS     │
 │ task-002 │ Leaves clean code alone  │ 2/3 │  97.3%  │ ✓ PASS     │
 │ task-003 │ Handles empty diff       │ 0/3 │   0.0%  │ ✗ FAIL     │
 └──────────┴──────────────────────────┴─────┴─────────┴────────────┘

 With skill    65.8%  ████████████░░░░░░░░
 No skill      18.3%  ████░░░░░░░░░░░░░░░░
 Delta        +47.5%  ↑
```

---

## Install

```bash
pip install -e .
```

Requires Python 3.10+ and an `ANTHROPIC_API_KEY` in your environment.

---

## Quick start

**1. Write a spec** (or use the wizard):

```yaml
# my-skill.eval.yaml
skill:
  path: ~/.claude/commands/my-skill.md
  backend: claude
  model: claude-sonnet-4-6

judge:
  backend: claude
  model: claude-haiku-4-5-20251001

tasks:
  - id: task-001
    name: Produces a valid output
    prompt: /my-skill do the thing
    expect: The output contains a valid result with no errors

  - id: task-002
    name: Writes an output file
    setup: mkdir -p /tmp/eval-work
    cleanup: rm -rf /tmp/eval-work
    prompt: /my-skill write output to /tmp/eval-work/out.txt
    expect: A file is created at /tmp/eval-work/out.txt
    assert: |
      import os
      assert os.path.exists("/tmp/eval-work/out.txt"), "File not created"
```

**2. Run it:**

```bash
verdict run my-skill.eval.yaml --k 3 --baseline
```

**3. Browse results:**

```bash
verdict list
verdict report my-skill
```

---

## Commands

| Command | Description |
|---|---|
| `verdict run <spec>` | Run an evaluation spec |
| `verdict new [name]` | Interactive wizard to author a spec |
| `verdict validate <spec>` | Schema-validate a spec file |
| `verdict list [spec]` | List specs and past runs with scores |
| `verdict report <spec>` | Re-render saved results |

### `verdict run` flags

| Flag | Default | Description |
|---|---|---|
| `--k INT` | `3` | Attempts per task |
| `--baseline` | off | Also run without the skill for delta scoring |
| `--judge {autorater,script}` | `autorater` | Judge strategy |
| `--workers INT` | `4` | Parallel task workers |
| `--timeout INT` | `120` | Seconds per attempt |
| `--verbose` | off | Show per-attempt judge reasoning |
| `--output PATH` | — | Save results JSON to a specific path |

---

## Spec format

```yaml
skill:
  path: ~/.claude/commands/my-skill.md   # path to the skill file
  backend: claude                         # claude | codex
  model: claude-sonnet-4-6               # optional model override

judge:
  backend: claude
  model: claude-haiku-4-5-20251001       # cheaper model is fine for judging

sandbox:
  forbidden_files:
    - ".*\\.eval\\.yaml$"                # agent cannot read the spec file
    - "./.verdict/.*"                    # agent cannot read stored results

tasks:
  - id: task-001
    name: <short description>
    setup: <shell command>               # optional: runs before each attempt
    cleanup: <shell command>             # optional: always runs after each attempt
    prompt: <prompt sent to the AI>
    expect: <natural language success description>   # used by the autorater
    assert: |                            # optional: inline Python assertion
      import os
      assert os.path.exists("/tmp/out"), "Missing output"

  - id: task-002
    name: Task with external assert script
    prompt: Generate a report
    assert: ./assertions/check_report.py  # or a path to a .py file
```

Each task needs at least one of `expect` or `assert`. When both are present, **both must pass**.

---

## How it works

### Isolation

Every attempt runs in a fresh temporary `HOME` directory with no session history, no user CLAUDE.md, and no memory files. The `CLAUDECODE` env var is stripped to allow nesting. Each attempt is hermetically isolated from all others.

### Judging

**Autorater** (default): The judge LLM reads the full conversation transcript — every assistant message, tool call input, and tool call output — then decides pass/fail against the `expect` description.

**Script judge** (`--judge script`): The judge LLM can either give a direct verdict or write a Python assertion script and run it. Pass = exit 0.

**Static assert** (`assert:` in YAML): A fixed Python script you write. Always runs alongside the autorater. Both must pass.

### Cheat detection

The transcript is scanned after every attempt for tool calls that accessed forbidden files (the spec itself, stored results, or any pattern listed in `sandbox.forbidden_files`). Cheating attempts are marked `⚠ CHEAT` and automatically fail.

### Scoring

`pass@k = 1 − (1 − p)^k` where `p = successes / k`. Averaged across all tasks to produce the aggregate score. With `--baseline`, the same tasks run without the skill and the delta is reported.

### Results storage

Results are saved to `.verdict/results/<spec-name>/<timestamp>.json` next to the spec file. Each result includes a full skill snapshot — the content of the skill `.md` file, any referenced scripts, and the git SHA if the skill is version-controlled.

---

## Agent usage

Copy `SKILL.md` to `~/.claude/commands/verdict.md` to let Claude Code invoke `verdict` as a skill:

```bash
cp SKILL.md ~/.claude/commands/verdict.md
```

Then in any Claude Code session: `/verdict run my-skill.eval.yaml --k 3`

---

## Backends

| Backend | Transcript | Skill injection |
|---|---|---|
| `claude` | Full tool-call history via `stream-json` | Temp `.md` in `.claude/commands/` |
| `codex` | Single text output | Skill body prepended as prompt context |
