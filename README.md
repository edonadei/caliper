# Caliper

[![PyPI](https://img.shields.io/pypi/v/caliper-eval.svg)](https://pypi.org/project/caliper-eval/)
[![Python](https://img.shields.io/pypi/pyversions/caliper-eval.svg)](https://pypi.org/project/caliper-eval/)

**pytest for agent skills.** Write a task in YAML, run it k times, get a reliability score.

```bash
npx skills@latest add edonadei/caliper
```

---

Agent skills are hard to test. A skill that works on your machine, on this prompt, today, might fail tomorrow after a model update or a one-line prompt edit. Caliper makes reliability measurable: define what success looks like, run the skill repeatedly, and get a pass@k score you can track over time.

Use Caliper to answer questions like:

- Did my prompt edit actually improve the skill?
- Is the skill doing the work, or would the base agent pass without it?
- Does it still pass the workflows it passed last week?
- Which backend — Claude Code or Codex — runs this skill more reliably?

![Caliper terminal demo](assets/caliper-demo.gif)

---

## How it works

```
.eval.yaml spec
      │
      ▼
  Harness  ──── runs your skill against the agent (Claude Code / Codex / API)
      │
      ▼
   Judge   ──── LLM autorater and/or deterministic Python assertions
      │
      ▼
  pass@k score + saved transcript
```

Each attempt runs in an isolated temporary home with no session history. Results are saved as JSON you can inspect and diff later.

---

## Quick start

**1. Install**

Install the `evaluate-skill` agent skill — it handles everything from inside your agent:

```bash
npx skills@latest add edonadei/caliper
```

Or install the CLI directly if you prefer running evals from the terminal:

```bash
pipx install caliper-eval
```

Requires Python 3.10+.

**2. Create a spec**

```yaml
# my-skill.eval.yaml
skill:
  path: ./SKILL.md
  backend: claude-code

judge:
  backend: claude-code

tasks:
  - name: Writes a greeting file
    cleanup: rm -f /tmp/hello.txt
    prompt: "Write 'hello world' to /tmp/hello.txt"
    expect: "A file at /tmp/hello.txt containing 'hello world' was created."
    assert: |
      from pathlib import Path
      assert Path("/tmp/hello.txt").read_text().strip() == "hello world"
```

**3. Run it**

If you installed via the skill, ask your agent:

```text
/evaluate-skill run my-skill.eval.yaml --k 3 --baseline
```

Or from the terminal if you installed the CLI:

```bash
caliper run my-skill.eval.yaml --k 3 --baseline
```

**4. Read the output**

```text
CALIPER  -  my-skill  -  k=3  -  claude-code

ID      Task                    k (3)   pass@k
task-1  Writes a greeting file  3/3     100%     PASS

With skill    100%    ####################
No skill       70%    ##############------
Delta          +30%   up

Results saved to .caliper/results/my-skill/2026-06-19T14-23-01Z.json
```

Browse past results anytime:

```text
/evaluate-skill list
/evaluate-skill report my-skill
```

---

## Core concepts

| Term | What it is |
|---|---|
| **Spec** | A `.eval.yaml` file that describes the skill, judge, and tasks to run |
| **Backend** | The agent that executes the skill (`claude-code`, `codex`, `claude-api`, `openai-api`) |
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

```bash
caliper run my-skill.eval.yaml --k 3 --baseline --verbose
```

---

## Spec format

```yaml
skill:
  path: ./SKILL.md              # path to the skill file (optional for baseline-only runs)
  backend: claude-code          # claude-code | codex | claude-api | openai-api
  model: <model-name>           # optional model override

judge:
  backend: claude-code          # claude-code | codex | claude-api | openai-api
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

### `--judge` flag

| Flag | Behavior |
|---|---|
| `--judge autorater` (default) | LLM judge evaluates `expect` |
| `--judge script` | Runs `assert:` always; also runs LLM judge if `expect` is present |

---

## CLI reference

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
| `--judge MODE` | `autorater` | `autorater` or `script` |
| `--workers INT` | `4` | Parallel task workers |
| `--timeout INT` | `120` | Seconds per attempt |
| `--model MODEL` | — | Override `skill.model` |
| `--verbose` | off | Show per-attempt judge reasoning |
| `--output PATH` | — | Also save results JSON to a specific path |

---

## Scoring

For each task:

```
pass@k = 1 - (1 - successes / k) ^ k
```

The aggregate score is the average task pass@k. With `--baseline`, Caliper runs the same tasks without the skill and reports the delta.

---

## Install the evaluator skill

The repo includes an `evaluate-skill` agent skill. Install it to let your agent create, validate, run, and summarize evals from inside your normal workflow — no separate terminal needed. The skill installs Caliper automatically if it's missing.

```bash
npx skills@latest add edonadei/caliper
```

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
Install and authenticate Claude Code, or switch the backend to `codex`, `claude-api`, or `openai-api`.

**A task passes only because of `assert:`**
When a task has only `assert:`, no LLM judge runs. Add `expect:` if you also want an LLM to evaluate the transcript.
