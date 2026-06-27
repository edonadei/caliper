# Caliper

[![PyPI](https://img.shields.io/pypi/v/caliper-eval.svg)](https://pypi.org/project/caliper-eval/)
[![Python](https://img.shields.io/pypi/pyversions/caliper-eval.svg)](https://pypi.org/project/caliper-eval/)
[![Skills](https://skills.sh/b/edonadei/caliper)](https://skills.sh/edonadei/caliper)

**Reliability testing for agent skills.** Run your skill _k_ times, get a pass@k score you can track and compare, and prove the skill beats the base agent. Works with the agent you already use — Claude Code, Codex, or Pi.

```bash
npx skills@latest add edonadei/caliper
```

Run each task with and without the skill, and Caliper shows you the difference:

```text
ID      Task                              k (3)   pass@k
task-1  Writes a conventional commit msg  3/3     100%     PASS
task-2  Generates a valid config file     2/3      96%     PASS

With skill     98%    ###################-
No skill       55%    ###########---------
Delta          +43%   up
```

---

Agent skills are hard to test. A skill that works on your machine, on this prompt, today, might fail tomorrow after a model update or a one-line prompt edit. Caliper makes reliability measurable: define what success looks like, run the skill repeatedly, and get a pass@k score you can track over time.

Use Caliper to answer questions like:

- Did my prompt edit actually improve the skill?
- Is the skill doing the work, or would the base agent pass without it?
- Does it still pass the workflows it passed last week?
- Which agent — Claude Code, Codex, or Pi — runs this skill more reliably?

---

## Quick start

### Path A — Agentic (let your agent drive)

**1. Install the skills**

```bash
npx skills@latest add edonadei/caliper
```

**2. Generate a spec interactively**

In your agent (Claude Code or Codex):

```text
/grill-skill ./my-skill/SKILL.md
```

`grill-skill` reads your `SKILL.md`, interviews you, and writes a 3-task `.eval.yaml` (happy path, edge case, adversarial).

**3. Run and measure**

```text
/evaluate-skill run my-skill.eval.yaml --k 3 --baseline
```

Browse past runs:

```text
/evaluate-skill list
/evaluate-skill report my-skill
```

### Path B — CLI (run it yourself)

**1. Install the CLI**

```bash
pipx install caliper-eval   # requires Python 3.10+
```

**2. Write a spec**

```yaml
# my-skill.eval.yaml
skill:
  path: ./SKILL.md
  backend: claude-code

judge:
  backend: claude-code

tasks:
  # Autorater — the LLM judge reads the transcript and decides
  - name: Writes a conventional commit message
    prompt: "Summarize the staged git diff as a commit message."
    expect: >
      The response is a conventional-commit message: a concise subject
      line under 72 characters, followed by a body explaining why the
      change was made, not just what changed.

  # Script execution — a deterministic Python assertion
  - name: Generates a valid config file
    cleanup: rm -f /tmp/app.config.json
    prompt: "Generate a config at /tmp/app.config.json with a 'port' of 8080."
    assert: |
      import json
      from pathlib import Path
      data = json.loads(Path("/tmp/app.config.json").read_text())
      assert data["port"] == 8080
```

`expect:` is graded by the judge LLM; `assert:` runs locally as Python. Use either or both.

**3. Run it**

```bash
caliper run my-skill.eval.yaml --k 3 --baseline
```

**4. Read the output**

```text
CALIPER  -  my-skill  -  k=3  -  claude-code

ID      Task                              k (3)   pass@k
task-1  Writes a conventional commit msg  3/3     100%     PASS
task-2  Generates a valid config file     2/3      96%     PASS

With skill     98%    ###################-
No skill       55%    ###########---------
Delta          +43%   up

Results saved to .caliper/results/my-skill/2026-06-19T14-23-01Z.json
```

---

## How it works

```
.eval.yaml spec
      │
      ▼
  Harness  ──── runs your skill against the agent (Claude Code / Codex / Pi)
      │
      ▼
   Judge   ──── LLM autorater and/or deterministic Python assertions
      │
      ▼
  pass@k score + saved transcript
```

Each attempt runs in an isolated temporary home with no session history. Results are saved as JSON you can inspect and diff later.

---

## Agent skills

The repo ships two agent skills. Install both with:

```bash
npx skills@latest add edonadei/caliper
```

### `evaluate-skill` — run and manage evals

Create, validate, run, and summarize evals from inside your normal workflow — no separate terminal needed. The skill installs Caliper automatically if it's missing.

Or, if you already have Caliper installed and want to wire up the skill manually:

```bash
caliper install-skill claude-code
caliper install-skill codex
```

Preview without writing files:

```bash
caliper install-skill claude-code --dry-run
```

Then use it in Claude Code:

```text
/evaluate-skill run my-skill.eval.yaml --k 3
/evaluate-skill validate my-skill.eval.yaml
```

Or in Codex:

```text
Use the evaluate-skill skill to run my-skill.eval.yaml with k=3 and summarize the result.
```

### `grill-skill` — create evals interactively

Don't have evals yet? `grill-skill` guides you through creating them. It reads your `SKILL.md`, interviews you about what good behavior looks like, and generates a 3-task spec (happy path, edge case, adversarial). Then it runs the eval and loops — k=1 to validate, k=3 to measure, baseline before you commit.

```text
/grill-skill ./my-skill/SKILL.md
```

No path needed if you're already in the skill's directory:

```text
/grill-skill
```

If an `.eval.yaml` already exists next to your skill, `grill-skill` reads the existing tasks and interviews you about gaps instead of starting from scratch.

---

## Core concepts

| Term | What it is |
|---|---|
| **Spec** | A `.eval.yaml` file that describes the skill, judge, and tasks to run |
| **Backend** | The agent that executes the skill (`claude-code`, `codex`, `pi`, `claude-api`, `openai-api`) |
| **Judge** | What decides pass/fail — an LLM reading the transcript (`expect:`), Python assertions (`assert:`), or both |
| **pass@k** | Reliability score: run k times, measure how often the skill succeeds |
| **Baseline** | Re-run the same tasks without the skill to prove the skill is doing the work |
| **Attempt** | One isolated run of a single task — fresh temporary home, no session history |

---

## Choosing a backend

| Backend | Requires | Best for |
|---|---|---|
| `claude-code` | Claude Code CLI installed and authenticated | Testing Claude Code slash-command skills |
| `codex` | Codex CLI installed (`npm install -g @openai/codex`) | Testing Codex skills |
| `pi` | pi CLI installed (`npm install -g @earendil-works/pi-coding-agent`) and authenticated | Testing pi skills (agentskills.io) |
| `claude-api` | `ANTHROPIC_API_KEY` env var | API-backed agents, no CLI needed |
| `openai-api` | `OPENAI_API_KEY` env var | OpenAI API agents |

The agent backend and judge backend are independent — you can test a Codex skill with a Claude judge, or any other combination.

### Claude Code setup

Install and authenticate the `claude` CLI. `backend: claude-code` uses your existing Claude Code auth — no extra configuration needed.

For `backend: claude-api`:

```bash
export ANTHROPIC_API_KEY=...
```

### Codex setup

```bash
npm install -g @openai/codex
codex login
```

`backend: codex` calls `codex exec`. It does not fall back to the OpenAI API. If the Codex desktop app is installed, Caliper prefers the app-bundled binary over `codex` on `PATH`. Set `CODEX_CLI_PATH` to force a specific binary.

For `backend: openai-api`:

```bash
export OPENAI_API_KEY=...
```

### pi setup

```bash
npm install -g @earendil-works/pi-coding-agent
pi   # then authenticate (e.g. /login for a subscription provider, or set the provider API key)
```

`backend: pi` runs `pi --print --mode json` and loads the skill natively via pi's `--skill` flag (the agentskills.io standard). It reuses your `~/.pi/agent` auth and settings — the spec's `model:` overrides pi's configured default when set. Set `PI_CLI_PATH` to force a specific binary. Note: pi's built-in default provider is `google`, so a spec with no `model:` relies on your pi config to resolve a provider you are authenticated for.

Check installed CLI versions:

```bash
caliper update-cli --check
```

---

## Recommended workflow

1. Create a spec for one behavior you care about.
2. Run with `--k 1` while iterating on the spec.
3. Add `assert:` for facts an LLM judge might guess wrong (files, JSON, command output).
4. Move to `--k 3` or higher once the task is stable.
5. Add `--baseline` to prove the skill is making a difference.
6. Commit the spec alongside the skill so contributors can run the same eval.

```text
/evaluate-skill run my-skill.eval.yaml --k 3 --baseline --verbose
```

---

## Spec format

```yaml
skill:
  path: ./SKILL.md              # path to the skill file (optional for baseline-only runs)
  backend: claude-code          # claude-code | codex | pi | claude-api | openai-api
  model: <model-name>           # optional model override

judge:
  backend: claude-code          # claude-code | codex | pi | claude-api | openai-api
  model: <model-name>           # optional model override

sandbox:
  extra_path:
    - ./bin                     # prepended to PATH inside each attempt
  forbidden_files:
    - ".*\\.eval\\.yaml$"       # prevents agent from reading the spec
    - "./.caliper/.*"           # prevents agent from reading saved results

tasks:
  - name: Short task name
    setup: <shell command>      # optional, runs before each attempt
    cleanup: <shell command>    # optional, always runs after each attempt
    prompt: <prompt sent to the agent>
    expect: <natural-language success condition>
    assert: |
      # optional inline Python assertion
      assert True

  - name: Task with external assertion script
    prompt: "Generate a report"
    assert: ./assertions/check_report.py
```

Each task needs at least one of `expect` or `assert`. Task IDs are assigned automatically as `task-001`, `task-002`, and so on.

---

## Judging

### LLM autorater (`expect:`)

The judge backend reads the full attempt transcript and decides whether the `expect` condition was met. When the backend captures tool-call traces (Claude Code, Codex), those traces are included — the judge can verify things like "the agent used tool X" without relying on the final text alone.

```yaml
judge:
  backend: claude-code
```

### Deterministic assertions (`assert:`)

Python assertions run locally. Use these for facts the LLM judge might guess:

- file exists / exact file contents
- JSON / schema validity
- command output
- images or screenshots
- repository state

```yaml
tasks:
  - name: Writes an output file
    cleanup: rm -f /tmp/out.txt
    prompt: "Write hello world to /tmp/out.txt"
    assert: |
      from pathlib import Path
      path = Path("/tmp/out.txt")
      assert path.exists(), "Output file was not created"
      assert path.read_text().strip() == "hello world"
```

When both `expect` and `assert` are present, both must pass.

---

## CLI reference

![Caliper terminal demo](assets/caliper-demo.gif)


| Command | Description |
|---|---|
| `caliper run <spec>` | Run an evaluation spec |
| `caliper new [name]` | Create a new spec with the interactive wizard |
| `caliper validate <spec>` | Validate a spec file |
| `caliper list [spec]` | List specs and saved runs |
| `caliper report <spec-or-result>` | Re-render saved results |
| `caliper install-skill <backend>` | Install the bundled evaluate-skill into Claude Code or Codex |
| `caliper update-cli [backend]` | Check or update installed agent CLI versions |

### `caliper run` flags

| Flag | Default | Description |
|---|---|---|
| `--k INT` | `3` | Attempts per task |
| `--baseline` | off | Also run each task without the skill |
| `--workers INT` | `4` | Parallel task workers |
| `--timeout INT` | `120` | Seconds per attempt |
| `--model TARGET` | — | Override skill backend and/or model (see below) |
| `--judge-model TARGET` | — | Override judge backend and/or model (see below) |
| `--verbose` | off | Show per-attempt judge reasoning |
| `--output PATH` | — | Also save results JSON to a specific path |

#### `--model` and `--judge-model` syntax

Both flags accept a `backend:model` compound value, a bare backend name, or a bare model name:

```bash
# Override backend and model together
caliper run my-skill.eval.yaml --model claude-api:claude-sonnet-4-6

# Override backend only (model stays unset / from spec)
caliper run my-skill.eval.yaml --model codex

# Override model only (backend stays from spec)
caliper run my-skill.eval.yaml --model claude-sonnet-4-6

# Override judge independently
caliper run my-skill.eval.yaml --model claude-api:claude-sonnet-4-6 --judge-model claude-api:claude-haiku-4-5-20251001
```

Accepted backends: `claude-code`, `codex`, `pi`, `claude-api`, `openai-api` (aliases: `claude`, `anthropic`, `openai`).

The spec file is never modified — overrides apply only to the current run.

---

## Scoring

For each task:

```
pass@k = 1 - (1 - successes / k) ^ k
```

The aggregate score is the average task pass@k. With `--baseline`, Caliper runs the same tasks without the skill and reports the delta.

---

## Project layout

```text
caliper/
  commands/       CLI command implementations
  harness/        Agent execution backends (Claude Code, Codex, API)
  judge/          LLM and script judging implementations
  schema/         Eval spec and result models
  runner.py       Evaluation orchestration
skills/
  evaluate-skill/ Agent skill for running Caliper from Claude Code or Codex
  grill-skill/    Agent skill for creating and iterating on evals interactively
tests/            Pytest coverage for harnesses, judges, and runner behavior
```

---

## Contributing

Good first areas:

- add example evals for real skills
- improve backend error messages
- add deterministic assertion helpers
- expand tests for harness and judge behavior
- improve result reporting and summaries
- document common setup problems for Claude Code and Codex

Before opening a pull request:

```bash
pip install -e ".[dev,openai]"
pytest
ruff check .
caliper validate skills/evaluate-skill/evaluate-skill.eval.yaml
```

When changing behavior, include a test or an eval fixture that demonstrates the expected outcome. Keep backend-specific logic isolated to the relevant module under `caliper/harness/` or `caliper/judge/`.

---

## Troubleshooting

**`codex judge failed: model ... is not supported`**
The model name is not available to your Codex account. Use a model that `codex exec --model <name>` accepts.

**`codex CLI not found`**

```bash
npm install -g @openai/codex
```

**`claude` command not found**
Install and authenticate Claude Code, or switch the backend to `codex`, `pi`, `claude-api`, or `openai-api`.

**A task passes only because of `assert:`**
When a task has only `assert:`, no LLM judge runs. Add `expect:` if you also want an LLM to evaluate the transcript.
