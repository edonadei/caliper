# Caliper

<p align="center">
  <img src="assets/logo.png" alt="Caliper logo" width="160">
</p>

Evaluate AI agent skills with repeatable tasks, automated judging, and pass@k
scoring.

Caliper is a local-first evaluation harness for Claude Code skills, Codex
skills, and API-backed agents. It runs a skill against one or more task specs,
records every attempt, judges the result with an LLM and/or deterministic Python
assertions, and saves reproducible result files you can inspect later.

Use Caliper when you want to answer practical questions like:

- Did this skill actually get better after my prompt edit?
- Does it still pass the workflows it passed last week?
- Does Codex or Claude Code run this skill more reliably for my use case?
- Is the skill doing the work, or would the baseline agent pass without it?
- Can contributors change a skill without relying on subjective manual testing?

Caliper is especially useful for agent skills because skills are hard to review
with ordinary unit tests. A good skill is part prompt, part workflow, part tool
contract. Caliper turns that behavior into versioned eval specs, repeatable
runs, pass/fail judgments, and saved transcripts.

## Highlights

- **Skill-first evaluation** for Claude Code, Codex, Anthropic API, and OpenAI
  API backends.
- **Independent agent and judge backends**, so you can test a Codex skill with a
  Claude judge, a Claude Code skill with a Codex judge, or keep everything on one
  provider.
- **Natural-language and deterministic checks** through `expect:` and `assert:`.
- **pass@k scoring** for measuring reliability across repeated attempts.
- **Baseline runs** to show whether the skill improves over an unassisted agent.
- **Attempt isolation** with fresh temporary homes and no session history.
- **Reproducible result files** that snapshot the skill content, referenced local
  files, and git SHA when available.
- **Agent-installable evaluator skill** so Claude Code or Codex can help create,
  validate, run, and interpret evals.

## When To Use It

Caliper works well for:

- evaluating Claude Code slash-command skills
- evaluating Codex skills
- comparing agent backends on the same task suite
- regression-testing prompt and workflow changes
- checking coding, review, refactor, summarization, screenshot, and file-writing
  behaviors
- mixing LLM judgment with exact checks for files, JSON, command output, images,
  and repository state

It is not a replacement for normal unit tests. Use unit tests for deterministic
library behavior. Use Caliper for agent behavior where the output depends on a
model following instructions, using tools, and completing a workflow.

## Install

From the repository root:

```bash
pip install -e .
```

For local development and optional OpenAI API support:

```bash
pip install -e ".[dev,openai]"
```

Caliper requires Python 3.10 or newer.

## Backend Setup

Caliper can run the agent under test and the judge through different backends.

| Role | Claude Code CLI | Codex CLI | API backends |
|---|---|---|---|
| Agent under test | `skill.backend: claude-code` | `skill.backend: codex` | `skill.backend: claude-api` or `openai-api` |
| LLM judge | `judge.backend: claude-code` | `judge.backend: codex` | `judge.backend: claude-api` or `openai-api` |
| Auth/billing | Claude Code subscription/auth | Codex CLI subscription/auth | Provider API key/billing |
| Transcript | Claude `stream-json` tool-call transcript | Final Codex text output | Final API response text |

### Claude Code

Install and authenticate the `claude` CLI. `backend: claude-code` uses your
normal Claude Code CLI auth.

If you explicitly use `backend: claude-api`, set:

```bash
export ANTHROPIC_API_KEY=...
```

### Codex

Install and authenticate the Codex CLI:

```bash
npm install -g @openai/codex
codex login
codex --version
```

`backend: codex` calls Codex with `codex exec`. It does not fall back to the
OpenAI API. If the CLI is unavailable or cannot authenticate, Caliper reports a
backend configuration error.

When the Codex desktop app is installed, Caliper prefers the app-bundled Codex
CLI over an older `codex` found on `PATH`. Set `CODEX_CLI_PATH` to force a
specific CLI binary.

If you explicitly use `backend: openai-api`, set:

```bash
export OPENAI_API_KEY=...
```

## Quick Start

Create an eval spec:

```yaml
# my-skill.eval.yaml
skill:
  path: ./SKILL.md
  backend: codex

judge:
  backend: codex

tasks:
  - name: Produces the expected answer
    prompt: "Use this skill to answer: what is 2 + 2?"
    expect: "The assistant answers 4."
```

Run it:

```bash
caliper run my-skill.eval.yaml --k 3 --baseline
```

Example output:

```text
CALIPER  -  my-skill  -  k=3  -  codex

ID      Task                           k (3)   pass@k
task-1  Produces the expected answer   2/3     96.3%    PARTIAL

With skill    96.3%   ###################-
No skill      70.4%   ##############------
Delta         +25.9%  up
Results saved to .caliper/results/my-skill/2026-05-22T14-23-01Z.json
```

Browse results:

```bash
caliper list
caliper report my-skill
```

Validate a spec before running:

```bash
caliper validate my-skill.eval.yaml
```

## Recommended Workflow

1. Create a small eval spec for one behavior you care about.
2. Run it with `--k 1` while iterating on the spec.
3. Add deterministic `assert:` checks for facts an LLM judge should not guess.
4. Run with `--k 3` or higher once the task is stable.
5. Use `--baseline` to measure whether the skill helps over the raw agent.
6. Commit the spec beside the skill so future contributors can run the same
   evaluation before changing behavior.

```bash
caliper run path/to/skill.eval.yaml --k 3 --baseline --verbose
```

## Install The Evaluator Skill

The repository includes an `evaluate-skill` agent skill. Installing it lets
Claude Code or Codex help you create eval specs, validate them, run Caliper, and
summarize results from inside your normal agent workflow.

### Claude Code

Copy the skill into Claude Code commands:

```bash
cp skills/evaluate-skill/SKILL.md ~/.claude/commands/evaluate-skill.md
```

Then use it in Claude Code:

```text
/evaluate-skill validate my-skill.eval.yaml
/evaluate-skill run my-skill.eval.yaml --k 3
```

### Codex

Install the skill in Codex:

```bash
mkdir -p ~/.codex/skills/evaluate-skill
cp skills/evaluate-skill/SKILL.md ~/.codex/skills/evaluate-skill/SKILL.md
```

Make sure `caliper` is on `PATH` for Codex sessions. If you installed Caliper in
editable mode, the generated console script is usually enough.

Then ask Codex:

```text
Use the evaluate-skill skill to validate my-skill.eval.yaml.
Use the evaluate-skill skill to run my-skill.eval.yaml with k=3 and summarize the result.
```

## Examples

### Codex Agent, Codex Judge

```yaml
skill:
  path: ./SKILL.md
  backend: codex

judge:
  backend: codex

tasks:
  - name: Validates a spec
    prompt: "Use caliper to validate ./example.eval.yaml and summarize the result."
    expect: "The assistant runs caliper validate and reports whether the spec is valid."
```

```bash
caliper run my-codex-skill.eval.yaml --k 1 --verbose
```

### Claude Code Agent, Claude Judge

```yaml
skill:
  path: ~/.claude/commands/review.md
  backend: claude-code
  model: claude-sonnet-4-6

judge:
  backend: claude-code
  model: claude-haiku-4-5-20251001

tasks:
  - name: Finds a null dereference
    prompt: "/review the staged changes in /tmp/eval-repo"
    expect: "The review identifies a possible null pointer dereference."
```

### Mix Backends

The agent backend and judge backend are independent:

```yaml
skill:
  path: ./SKILL.md
  backend: codex

judge:
  backend: claude-code
```

Or opt into API billing explicitly:

```yaml
skill:
  path: ./SKILL.md
  backend: openai-api
  model: gpt-4o-mini

judge:
  backend: openai-api
  model: gpt-4o-mini
```

### Deterministic Assertions

Use `assert:` when success can be verified with Python. This is usually better
than asking an LLM to judge files, JSON, command output, or screenshots.

```yaml
tasks:
  - name: Writes an output file
    cleanup: rm -f /tmp/out.txt
    prompt: "Write hello world to /tmp/out.txt"
    expect: "A file is written at /tmp/out.txt."
    assert: |
      from pathlib import Path

      path = Path("/tmp/out.txt")
      assert path.exists(), "Output file was not created"
      assert path.read_text().strip() == "hello world"
```

When both `expect` and `assert` are present, both must pass.

### Screenshot Skill Eval

The repo includes a Codex-backed screenshot eval:

```bash
caliper validate skills/evaluate-skill/references/evals/screenshot/screenshot.eval.yaml
caliper run skills/evaluate-skill/references/evals/screenshot/screenshot.eval.yaml --k 1 --judge script --verbose
```

On macOS, the process running the eval must have Screen Recording permission. If
direct `screencapture -x /tmp/test.png` fails, this eval will fail until that
permission is granted.

## Spec Format

```yaml
skill:
  path: ./SKILL.md              # optional path to the skill file
  backend: codex                # claude-code | codex | claude-api | openai-api
  model: <model-name>           # optional backend-specific model override

judge:
  backend: codex                # claude-code | codex | claude-api | openai-api
  model: <model-name>           # optional backend-specific model override

sandbox:
  extra_path:
    - ./bin                     # optional paths prepended to PATH
  forbidden_files:
    - ".*\\.eval\\.yaml$"       # agent cannot read the spec file
    - "./.caliper/.*"           # agent cannot read saved results

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

Each task must define at least one of `expect` or `assert`. Task ids are assigned
automatically as `task-001`, `task-002`, and so on.

## Commands

| Command | Description |
|---|---|
| `caliper run <spec>` | Run an evaluation spec |
| `caliper new [name]` | Create a new evaluation spec with the wizard |
| `caliper validate <spec>` | Validate a spec file |
| `caliper list [spec]` | List specs and saved runs |
| `caliper report <spec-or-result>` | Re-render saved results |

### `caliper run` Flags

| Flag | Default | Description |
|---|---|---|
| `--k INT` | `3` | Attempts per task |
| `--baseline` | off | Also run each task without the skill |
| `--judge autorater` | `autorater` | LLM judge gives a direct pass/fail |
| `--judge script` | | Run static assertions and, if `expect` exists, an LLM judge |
| `--judge autorater-sdk` | | Legacy alias for Anthropic SDK judging; prefer `judge.backend: claude-api` |
| `--workers INT` | `4` | Parallel task workers |
| `--timeout INT` | `120` | Seconds per attempt |
| `--model MODEL` | | Override `skill.model` for the agent under test |
| `--verbose` | off | Show per-attempt judge reasoning |
| `--output PATH` | | Also save results JSON to a specific path |

## Judging

### Autorater

`--judge autorater` asks the configured judge backend to decide whether the
transcript satisfies `expect`.

```yaml
judge:
  backend: codex
```

### Script Judge

`--judge script` always runs static `assert:` checks when present.

If the task also has `expect`, it also asks the configured judge backend for an
LLM verdict. With `judge.backend: codex`, that LLM check is performed by Codex
CLI. With `judge.backend: claude-code`, it is performed by Claude Code CLI. Use
`claude-api` or `openai-api` only when API billing is intended.

### Static Assertions

Static assertions run locally with Python. They are ideal for verifying:

- files exist
- exact file contents
- JSON/schema validity
- command output
- images or screenshots
- repository state

## Isolation And Reproducibility

Each attempt runs with a fresh temporary `HOME` directory. For Claude Code,
Caliper installs a temporary slash-command skill in that isolated home. For
Codex, Caliper injects the skill body directly into the prompt passed to
`codex exec`.

Results are saved next to the spec:

```text
.caliper/results/<spec-name>/<timestamp>.json
```

Each result includes a skill snapshot: the skill file content, referenced local
files, and git SHA when available.

## Scoring

For each task:

```text
pass@k = 1 - (1 - successes / k)^k
```

The aggregate score is the average task pass@k. With `--baseline`, Caliper also
runs the same tasks without the skill and reports the delta.

## Project Layout

```text
caliper/
  commands/       Typer command implementations
  harness/        Claude, Codex, and API execution backends
  judge/          LLM and script judging implementations
  schema/         Eval spec and result models
  runner.py       Evaluation orchestration
skills/
  evaluate-skill/ Agent skill for running Caliper from Claude Code or Codex
tests/            Pytest coverage for harnesses, judges, and runner behavior
```

## Contributing

Contributions are welcome when they keep Caliper focused on repeatable,
maintainable skill evaluation.

Good first contribution areas:

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

When changing behavior, include either a test or an eval fixture that demonstrates
the expected outcome. Keep backend-specific behavior isolated to the relevant
module under `caliper/harness/` or `caliper/judge/` when possible.

## Troubleshooting

### `codex judge failed: model ... is not supported`

The model name in `skill.model` or `judge.model` is not available to your Codex
account. Use a model that `codex exec --model <name>` supports.

### `codex CLI not found`

Install the Codex CLI and ensure it is on `PATH`:

```bash
npm install -g @openai/codex
```

### `claude` command not found

Install and authenticate Claude Code, or switch the relevant backend to `codex`,
`claude-api`, or `openai-api`.

### A task passes only because of `assert:`

When a task has only `assert:`, no LLM judge is required. Add `expect:` if you
also want an LLM to judge the transcript.
