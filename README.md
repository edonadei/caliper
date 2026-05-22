# caliper

Evaluate AI agent skills with repeatable tasks, automated judging, and pass@k
scoring.

`caliper` runs a skill against one or more tasks, records each attempt, judges
the result with an LLM and/or deterministic Python assertions, and saves a
reproducible result file.

It supports both Claude Code and Codex:

| Role | Claude Code | Codex |
|---|---|---|
| Agent under test | `skill.backend: claude` | `skill.backend: codex` |
| LLM judge | `judge.backend: claude` | `judge.backend: codex` |
| Skill injection | Temporary slash command in isolated `.claude/commands/` | Skill body prepended to the Codex prompt |
| Transcript | Claude `stream-json` tool-call transcript | Final Codex text output |

---

## Install

```bash
pip install -e .
```

For local development:

```bash
pip install -e ".[dev,codex]"
```

### Claude Code setup

Install and authenticate the `claude` CLI. `caliper` uses the CLI by default for
Claude-backed agents and judges, so OAuth/file credentials from your normal
Claude Code setup can be reused.

If you explicitly use `--judge autorater-sdk`, you also need:

```bash
export ANTHROPIC_API_KEY=...
```

### Codex setup

Install and authenticate the Codex CLI:

```bash
npm install -g @openai/codex
codex login
codex --version
```

`caliper` calls Codex with `codex exec`. If the CLI is not available, the Codex
agent backend falls back to the OpenAI SDK and requires:

```bash
export OPENAI_API_KEY=...
```

The Codex judge uses the Codex CLI.

---

## Quick Start

Create an eval spec:

```yaml
# my-skill.eval.yaml
skill:
  path: ./SKILL.md
  backend: codex
  model: gpt-5.4-mini

judge:
  backend: codex
  model: gpt-5.4-mini

tasks:
  - name: Produces the expected answer
    prompt: "Use this skill to answer: what is 2 + 2?"
    expect: "The assistant answers 4."
```

Run it:

```bash
caliper run my-skill.eval.yaml --k 3
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

---

## Examples

### Codex Agent, Codex Judge

Use Codex both for the agent under test and for the natural-language judge:

```yaml
skill:
  path: ./SKILL.md
  backend: codex
  model: gpt-5.4-mini

judge:
  backend: codex
  model: gpt-5.4-mini

tasks:
  - name: Validates a spec
    prompt: "Use caliper to validate ./example.eval.yaml and summarize the result."
    expect: "The assistant runs caliper validate and reports whether the spec is valid."
```

```bash
caliper run my-codex-skill.eval.yaml --k 1 --verbose
```

### Claude Code Agent, Claude Judge

Use Claude Code for both the agent under test and the judge:

```yaml
skill:
  path: ~/.claude/commands/review.md
  backend: claude
  model: claude-sonnet-4-6

judge:
  backend: claude
  model: claude-haiku-4-5-20251001

tasks:
  - name: Finds a null dereference
    prompt: "/review the staged changes in /tmp/eval-repo"
    expect: "The review identifies a possible null pointer dereference."
```

### Mix Backends

The agent backend and judge backend are independent. For example, test a Codex
skill with a Claude judge:

```yaml
skill:
  path: ./SKILL.md
  backend: codex

judge:
  backend: claude
```

Or test a Claude Code skill with a Codex judge:

```yaml
skill:
  path: ~/.claude/commands/review.md
  backend: claude

judge:
  backend: codex
  model: gpt-5.4-mini
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
caliper validate evals/screenshot/screenshot.eval.yaml
caliper run evals/screenshot/screenshot.eval.yaml --k 1 --judge script --verbose
```

That eval uses:

- `skill.backend: codex`
- `judge.backend: codex`
- a static PNG assertion to verify the screenshot file was created
- an `expect` field so the Codex judge also checks the transcript

---

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
| `--judge autorater-sdk` | | Use the Anthropic SDK judge explicitly |
| `--workers INT` | `4` | Parallel task workers |
| `--timeout INT` | `120` | Seconds per attempt |
| `--model MODEL` | | Override `skill.model` for the agent under test |
| `--verbose` | off | Show per-attempt judge reasoning |
| `--output PATH` | | Also save results JSON to a specific path |

---

## Spec Format

```yaml
skill:
  path: ./SKILL.md              # optional path to the skill file
  backend: codex                # claude | codex
  model: gpt-5.4-mini           # optional model override

judge:
  backend: codex                # claude | codex
  model: gpt-5.4-mini           # optional model override

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

Each task must define at least one of `expect` or `assert`.

Task ids are assigned automatically as `task-001`, `task-002`, and so on.

---

## Judging

### Autorater

`--judge autorater` asks the configured judge backend to decide whether the
transcript satisfies `expect`.

```yaml
judge:
  backend: codex
  model: gpt-5.4-mini
```

or:

```yaml
judge:
  backend: claude
  model: claude-haiku-4-5-20251001
```

### Script Judge

`--judge script` always runs static `assert:` checks when present.

If the task also has `expect`, it also asks the configured judge backend for an
LLM verdict. With `judge.backend: codex`, that LLM check is performed by Codex.
With `judge.backend: claude`, it is performed by Claude/Anthropic.

### Static Assertions

Static assertions run locally with Python. They are ideal for verifying:

- files exist
- exact file contents
- JSON/schema validity
- command output
- images or screenshots
- repository state

---

## Isolation and Reproducibility

Each attempt runs with a fresh temporary `HOME` directory. For Claude Code,
`caliper` installs a temporary slash-command skill in that isolated home. For
Codex, `caliper` injects the skill body directly into the prompt passed to
`codex exec`.

Results are saved next to the spec:

```text
.caliper/results/<spec-name>/<timestamp>.json
```

Each result includes a skill snapshot: the skill file content, referenced local
files, and git SHA when available.

---

## Scoring

For each task:

```text
pass@k = 1 - (1 - successes / k)^k
```

The aggregate score is the average task pass@k. With `--baseline`, `caliper`
also runs the same tasks without the skill and reports the delta.

---

## Install `caliper` as an Agent Skill

### Claude Code

Copy the repo skill into your Claude commands:

```bash
cp SKILL.md ~/.claude/commands/caliper.md
```

Then use it in Claude Code:

```text
/caliper run my-skill.eval.yaml --k 3
```

### Codex

Install the skill in Codex:

```bash
mkdir -p ~/.codex/skills/caliper
cp SKILL.md ~/.codex/skills/caliper/SKILL.md
```

Make sure `caliper` is on PATH for Codex sessions. If you installed in editable
mode, the generated console script is usually enough. On Windows, you can create
a `caliper.cmd` shim in a PATH directory if needed.

Then ask Codex:

```text
Use the caliper skill to validate my-skill.eval.yaml.
```

---

## Troubleshooting

### `codex judge failed: model ... is not supported`

The model name in `skill.model` or `judge.model` is not available to your Codex
account. Use a model that `codex exec --model <name>` supports.

### `codex CLI not found`

Install the Codex CLI and ensure it is on PATH:

```bash
npm install -g @openai/codex
```

### `claude` command not found

Install and authenticate Claude Code, or switch the relevant backend to Codex.

### A task passes only because of `assert:`

When a task has only `assert:`, no LLM judge is required. Add `expect:` if you
also want an LLM to judge the transcript.
