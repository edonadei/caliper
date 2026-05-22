# caliper

Evaluate AI agent skills with repeatable tasks, automated judging, and pass@k
scoring.

`caliper` runs a skill against one or more tasks, records each attempt, judges
the result with an LLM and/or deterministic Python assertions, and saves a
reproducible result file.

It supports Claude Code, Codex, and explicit API backends:

| Role | Claude Code CLI | Codex CLI | API backends |
|---|---|---|---|
| Agent under test | `skill.backend: claude-code` | `skill.backend: codex` | `skill.backend: claude-api` or `openai-api` |
| LLM judge | `judge.backend: claude-code` | `judge.backend: codex` | `judge.backend: claude-api` or `openai-api` |
| Auth/billing | Claude Code subscription/auth | Codex CLI subscription/auth | Provider API key/billing |
| Transcript | Claude `stream-json` tool-call transcript | Final Codex text output | Final API response text |

---

## Install

```bash
pip install -e .
```

For local development:

```bash
pip install -e ".[dev,openai]"
```

### Claude Code setup

Install and authenticate the `claude` CLI. `backend: claude-code` uses the CLI,
so OAuth/file credentials from your normal Claude Code setup can be reused.

If you explicitly use `backend: claude-api`, you also need:

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

`backend: codex` calls Codex with `codex exec`. It does not fall back to the
OpenAI API. If the CLI is unavailable or cannot authenticate, Caliper reports a
backend configuration error.

When the Codex desktop app is installed, Caliper prefers the app-bundled Codex
CLI over an older `codex` found on `PATH`. Set `CODEX_CLI_PATH` to force a
specific CLI binary.

If you explicitly use `backend: openai-api`, you also need:

```bash
export OPENAI_API_KEY=...
```

The Codex judge also uses the Codex CLI.

---

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

Use Claude Code for both the agent under test and the judge:

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

The agent backend and judge backend are independent. For example, test a Codex
skill with a Claude Code judge:

```yaml
skill:
  path: ./SKILL.md
  backend: codex

judge:
  backend: claude-code
```

Or test a Claude Code skill with a Codex judge:

```yaml
skill:
  path: ~/.claude/commands/review.md
  backend: claude-code

judge:
  backend: codex
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
caliper validate evals/screenshot/screenshot.eval.yaml
caliper run evals/screenshot/screenshot.eval.yaml --k 1 --judge script --verbose
```

That eval uses:

- `skill.backend: codex`
- `judge.backend: codex`
- a static PNG assertion to verify the screenshot file was created

On macOS, the process running the eval must have Screen Recording permission.
If direct `screencapture -x /tmp/test.png` fails, this eval will fail until that
permission is granted.

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
| `--judge autorater-sdk` | | Legacy alias for Anthropic SDK judging; prefer `judge.backend: claude-api` |
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
  backend: codex                # claude-code | codex | claude-api | openai-api

judge:
  backend: codex                # claude-code | codex | claude-api | openai-api

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
```

or:

```yaml
judge:
  backend: claude-code
  model: claude-haiku-4-5-20251001
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

## Install the Skill Evaluator as an Agent Skill

### Claude Code

Copy the repo skill into your Claude commands:

```bash
cp skills/evaluate-skill/SKILL.md ~/.claude/commands/evaluate-skill.md
```

Then use it in Claude Code:

```text
/evaluate-skill run my-skill.eval.yaml --k 3
```

### Codex

Install the skill in Codex:

```bash
mkdir -p ~/.codex/skills/evaluate-skill
cp skills/evaluate-skill/SKILL.md ~/.codex/skills/evaluate-skill/SKILL.md
```

Make sure `caliper` is on PATH for Codex sessions. If you installed in editable
mode, the generated console script is usually enough. On Windows, you can create
a `caliper.cmd` shim in a PATH directory if needed.

Then ask Codex:

```text
Use the evaluate-skill skill to validate my-skill.eval.yaml.
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

Install and authenticate Claude Code, or switch the relevant backend to `codex`, `claude-api`, or `openai-api`.

### A task passes only because of `assert:`

When a task has only `assert:`, no LLM judge is required. Add `expect:` if you
also want an LLM to judge the transcript.
