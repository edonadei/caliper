# Caliper — Reliability testing for agent skills

[![PyPI](https://img.shields.io/pypi/v/caliper-eval.svg)](https://pypi.org/project/caliper-eval/)
[![Python](https://img.shields.io/pypi/pyversions/caliper-eval.svg)](https://pypi.org/project/caliper-eval/)
[![Skills](https://skills.sh/b/edonadei/caliper)](https://skills.sh/edonadei/caliper)

Know whether your skill actually works. Write a short spec of what "good" looks like, run it _k_ times, and get a pass@k score you can track. Caliper also runs the tasks without the skill, so you can see whether it's the skill or the base agent doing the work. Works with the agent you already use: Claude Code, Codex, Pi, or Hermes.

**Teach your agent to evaluate:**

```bash
npx skills@latest add edonadei/caliper
```

**Or run it yourself:**

```bash
caliper run my-skill.eval.yaml --k 3 --baseline
```

That command reads a spec: a few lines of YAML describing what "working" means, which you hand-write or have `/grill-skill` generate for you. Caliper runs each task with and without the skill, then shows you the difference:

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
- Which agent — Claude Code, Codex, Pi, or Hermes — runs this skill more reliably?

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

The spec never names an engine — the skill and judge default to `claude-code`, and you pick a different agent/model at run time with `--model` / `--judge-model` (see [Choosing an engine](#choosing-an-engine)).

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

### Not sure what to put in a spec?

The **[Eval Starter Pack](examples/starter-pack/)** has four copy-paste
templates, each catching a real agent failure (false success, tool misuse,
runaway loops, prompt regressions). Every template runs green as-is against a
bundled example, then points at your own skill by editing two or three
commented lines.

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
| **Backend** | The CLI agent that executes the skill (`claude-code`, `codex`, `pi`, `hermes`) |
| **Judge** | What decides pass/fail — an LLM reading the transcript (`expect:`), Python assertions (`assert:`), or both |
| **pass@k** | Reliability score: run k times, measure how often the skill succeeds |
| **Baseline** | Re-run the same tasks without the skill to prove the skill is doing the work |
| **Attempt** | One isolated run of a single task — fresh temporary home, no session history |

---

## Choosing an engine

The engine (backend + model) is a **runtime axis, not a spec field** — the spec
describes *what* is tested and *how* success is judged, and you pick the agent
that runs and grades it at invocation. Both default to `claude-code`; select a
different one with `--model` / `--judge-model`:

```bash
caliper run my-skill.eval.yaml                          # claude-code (default)
caliper run my-skill.eval.yaml --model codex            # codex, its default model
caliper run my-skill.eval.yaml --model codex:gpt-5-codex
caliper run my-skill.eval.yaml --model pi --judge-model claude-code
```

| Backend | Requires | Best for |
|---|---|---|
| `claude-code` | Claude Code CLI installed and authenticated | Testing Claude Code slash-command skills |
| `codex` | Codex CLI installed (`npm install -g @openai/codex`) | Testing Codex skills |
| `pi` | pi CLI installed (`npm install -g @earendil-works/pi-coding-agent`) and authenticated | Testing pi skills (agentskills.io) |
| `hermes` | Hermes Agent CLI installed and authenticated (Nous Research) | Testing skills on Hermes; `hermes:<provider>/<model>` selects the model |

Caliper runs skills only through CLI agents — every backend can actually load and run a skill. There is no direct-API backend: to run against API-priced billing, configure one of these CLIs with an API key (e.g. `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) rather than selecting a separate backend.

The skill engine and judge engine are independent — you can test a Codex skill with a Claude judge, or any other combination, by pairing `--model` with `--judge-model`.

### Claude Code setup

Install and authenticate the `claude` CLI. `--model claude-code` uses your existing Claude Code auth — no extra configuration needed.

### Codex setup

```bash
npm install -g @openai/codex
codex login
```

`--model codex` calls `codex exec`. If the Codex desktop app is installed, Caliper prefers the app-bundled binary over `codex` on `PATH`. Set `CODEX_CLI_PATH` to force a specific binary.

### pi setup

```bash
npm install -g @earendil-works/pi-coding-agent
pi   # then authenticate (e.g. /login for a subscription provider, or set the provider API key)
```

`--model pi` runs `pi --print --mode json` and loads the skill natively via pi's `--skill` flag (the agentskills.io standard). It reuses your `~/.pi/agent` auth and settings — the `:model` half of `--model pi:<model>` overrides pi's configured default when set. Set `PI_CLI_PATH` to force a specific binary. Note: pi's built-in default provider is `google`, so running `--model pi` with no model relies on your pi config to resolve a provider you are authenticated for.

### Hermes setup

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes login   # authenticate; pick a default model/provider you have credits for
```

Hermes is a stateful, always-on agent (persistent memory, a persona, auto-generated skills), so Caliper **normalizes it to a neutral agent** to keep its pass@k apples-to-apples with the other backends: every attempt runs in an isolated `HERMES_HOME` seeded with your `~/.hermes` auth/config only (never `SOUL.md`/`MEMORY.md`) and with `--ignore-rules`, and the skill-under-test is staged as the sole local skill. `--model hermes` runs `hermes -z` (oneshot) then `hermes sessions export` to recover the full tool-call trajectory; `--model hermes:<provider>/<model>` (e.g. `hermes:anthropic/claude-sonnet-4.6`) selects the model, otherwise your `~/.hermes/config.yaml` default is used — point it at a provider you have credits for. Set `HERMES_CLI_PATH` to force a specific binary. Hermes updates itself (`hermes update`), so it is not part of `caliper update-cli`.

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

To scaffold a spec, use the [`evaluate-skill`](#evaluate-skill--run-and-manage-evals)
or [`grill-skill`](#grill-skill--create-evals-interactively) skill, or hand-write
the YAML below.

```yaml
skill:
  path: ./SKILL.md              # path to the skill file (optional for baseline-only runs)

# Note: there is no `backend`/`model` or `judge:` block. The engine is a runtime
# axis — pass `--model` / `--judge-model` at run time (default: claude-code).

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

The judge engine reads the full attempt transcript and decides whether the `expect` condition was met. When the backend captures tool-call traces (Claude Code, Codex, pi, Hermes), those traces are included — the judge can verify things like "the agent used tool X" without relying on the final text alone.

The judge engine is chosen at run time and defaults to `claude-code`; point it at a different agent with `--judge-model` (e.g. `--judge-model codex`), independently of the skill's `--model`.

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

| Command | Description |
|---|---|
| `caliper run <spec>` | Run an evaluation spec |
| `caliper validate <spec>` | Validate a spec file |
| `caliper list [spec]` | List specs and saved runs |
| `caliper report <spec-or-result>` | Re-render saved results |
| `caliper compare <A> <B>` | Diff two saved runs of the same eval, task by task |
| `caliper update-cli [backend]` | Check or update installed agent CLI versions |

### `caliper run` flags

| Flag | Default | Description |
|---|---|---|
| `--k INT` | `3` | Attempts per task |
| `--baseline` | off | Also run each task without the skill |
| `--workers INT` | `4` | Parallel task workers |
| `--timeout INT` | `120` | Seconds per attempt |
| `--fail-fast INT` | `0` | Stop a task after N consecutive `infra_error`/`timeout` attempts (`0` disables) |
| `--model TARGET` | `claude-code` | Skill engine — backend and/or model (see below) |
| `--judge-model TARGET` | `claude-code` | Judge engine — backend and/or model (see below) |
| `--verbose` | off | Show per-attempt judge reasoning |
| `--output PATH` | — | Also save results JSON to a specific path |

#### `--model` and `--judge-model` syntax

The engine is not stored in the spec — these flags select it, defaulting to `claude-code` when omitted. Both accept a `backend:model` compound value, a bare backend name, or a bare model name:

```bash
# Backend and model together
caliper run my-skill.eval.yaml --model codex:gpt-5-codex

# Backend only (that backend's default model)
caliper run my-skill.eval.yaml --model codex

# Model only (backend stays claude-code)
caliper run my-skill.eval.yaml --model claude-sonnet-4-6

# Select the judge engine independently
caliper run my-skill.eval.yaml --model codex --judge-model claude-code:claude-haiku-4-5-20251001
```

Accepted backends: `claude-code`, `codex`, `pi`, `hermes` (alias: `claude` → `claude-code`). The actual engine used is recorded in each saved run's `RunMeta` — the skill `backend`/`model`, and the `judge_backend`/`judge_model` that graded it — so results stay traceable even though the spec doesn't pin it. When you don't name a model and the CLI uses its own default, `RunMeta` records the concrete model the agent resolved rather than a bare "default", wherever the backend reports it — the skill model from hermes' session export, and the `judge_model` from the `claude-code` judge's JSON output. `judge_model` stays empty for an `assert:`-only run, where no LLM judge fired.

---

## Comparing two runs (`caliper compare`)

An **ablation** compares two runs of the *same* eval — a full skill vs. a
shortened variant, or the same skill over time. `caliper compare <A> <B>` diffs
two already-saved runs task by task, so you don't hand-write a JSON script to
answer "did this change regress?".

```bash
# Latest run of each spec (a bare spec name resolves to its latest run)
caliper compare commit-simple-full commit-simple-short

# Pin specific runs by pointing at their results JSON
caliper compare .caliper/results/demo/2026-07-01T10-00-00Z.json \
                .caliper/results/demo/2026-07-02T09-00-00Z.json

# Machine-readable diff for a ship / no-ship decision
caliper compare A B --format json
```

Each positional (`A`, `B`) is addressed exactly like `report`'s argument: a spec
name (→ its latest run) or a path to a results JSON. There are no `--run-a/-b`
flags — pin a historical run by naming its JSON path.

```
──────────────────── CALIPER  —  compare  —  commit-simple ─────────────────────
    A 2026-07-01T10-00-00Z (claude-code)   ·   B 2026-07-02T09-00-00Z (claude-code)   ·   k=5

╭──────────────────┬──────────┬──────────┬────────┬─────────┬─────────╮
│ Task             │ A pass@k │ B pass@k │      Δ │ A strip │ B strip │
├──────────────────┼──────────┼──────────┼────────┼─────────┼─────────┤
│ commits cleanly  │   100.0% │   100.0% │      — │ ✓✓✓✓✓   │ ✓✓✓✓✓   │
│ handles conflict │    80.0% │    40.0% │ -40.0% │ ✓✓✓✓✗   │ ✓✗✓✗✗   │
│ pushes upstream  │    80.0% │        — │      — │ ✓✓✓✓✗   │ ⊘⊘⊘⊘⊘   │
╰──────────────────┴──────────┴──────────┴────────┴─────────┴─────────╯

 A 90.0%   B 70.0%   Δ (matched) -20.0% ↓
 Tokens  A 1.2M  B 0.7M   Δ -42% (-500K)
 Wall    A 6m 18s  B 3m 40s   Δ -42% (-2m 38s)
 ⚠ 1 regression: handles conflict
 ⊘ 1 unmeasured (excluded from Δ): pushes upstream
 unmatched — only in A: flaky task   only in B: new task
```

How the diff reads:

- **Tasks are matched by name.** `task_id` is only positional, so name is the
  stable identity — reordered tasks still line up. A task present in only one
  run is listed as **unmatched** and left out of the delta.
- **`Δ` is `b − a`.** A negative Δ renders red and flags the task as a
  **regression** (any-below rule: B below A by any amount).
- **pass@k excludes unusable attempts.** The strips reuse the run report's
  glyphs; `⊘` marks an unusable attempt (rate-limit / timeout / judge error).
  A task with *no* usable attempts on a side shows `—` (unmeasured) and is never
  counted as a regression — infra noise can't fake a loss.
- **The headline `Δ (matched)`** averages each side over only the tasks measured
  on **both** sides, so it is strictly like-for-like.
- **Token and wall-clock deltas** sit under the headline as *secondary* signals:
  a drop renders green (cheaper), a rise red (costlier). They are **never** a
  regression — a token drop at flat pass@k is the win an ablation is looking for,
  and a rise is a trade-off to weigh, not a failure. Only pass@k feeds
  `has_regression`. The token row is shown only when both runs reported tokens;
  wall time is always shown. Dollar cost is deliberately not tracked (tokens are
  the volume signal).
- **Guards** for a `k` mismatch or different spec names print as warnings in the
  header *and* in `--format json` (`k_mismatch`, `spec_mismatch`, `warnings`), so
  an agent driving `compare` sees them too.

The `--format json` output serializes the full comparison (per-task scores,
deltas, `regression`/`has_regression` flags, unmatched task lists, the warnings,
and per-side usage totals `a_usage`/`b_usage` with `token_delta`/`wall_delta`)
for scripting.

---

## Scoring

Every attempt carries a typed **outcome**, so infrastructure and judge noise are
not scored as task failure:

| Outcome | Meaning | Counts toward pass@k? |
| --- | --- | --- |
| `pass` | satisfied the task's judge(s) | ✅ success |
| `task_fail` | the skill genuinely failed the task | ✅ attempt |
| `cheat` | a forbidden-file read was detected | ✅ attempt |
| `infra_error` | harness failure — nonzero exit, or a detected rate-limit / spending-cap | ❌ unusable |
| `timeout` | exceeded the time budget with no result | ❌ unusable |
| `judge_error` | the judge produced no verdict (unparseable / errored autorater) | ❌ unusable |

`passed` is retained in the JSON as a convenience, equal to `outcome == pass`.

For each task, pass@k is computed over the **usable** attempts (those that got a
fair shot); unusable attempts leave the denominator and are reported as a
separate "N unusable" count:

```
usable  = pass + task_fail + cheat
pass@k  = 1 - (1 - successes / usable) ^ usable      # None if usable == 0
```

The aggregate score is the average task pass@k, skipping tasks with no usable
attempts. With `--baseline`, Caliper runs the same tasks without the skill and
reports the delta. A throttled or judge-flaked run therefore no longer
masquerades as a regression.

For persistent infrastructure failures, `--fail-fast N` can stop scheduling new
attempts for a task after N consecutive `infra_error` or `timeout` outcomes.
The default `0` keeps the historical behavior and runs all k attempts. An
early-stopped task is shown as `ABORTED` in the report; if every completed
attempt was unusable, its `pass_at_k` remains `null` and it is skipped in the
aggregate score.

---

## Token & wall-clock usage

Pass@k tells you *whether* a skill works; usage tells you what it **costs** to
get there. Two runs can have identical pass@k while one burns twice the tokens.
Caliper records **token volume** and **wall-clock time** per attempt and rolls
them up per run:

```
 With skill    100.0%  ████████████████████
 Tokens   1.2M in / 340K out   ·   Wall 6m 18s (avg 12.6s/usable)
 ⊘ unusable spend: 180K tokens · 42s  (2 attempts, excluded from avg)
```

- Each `AttemptRecord` carries an optional `usage` object with `input_tokens`
  (non-cached prompt), `output_tokens`, `cache_read_tokens`,
  `cache_creation_tokens`, and a computed `total_tokens`. The four token fields
  are **disjoint** — `input_tokens` excludes cache — so `total_tokens` never
  double-counts. `duration_seconds` (already recorded) is the wall-clock half.
- **`in` = input + cache_read + cache_creation; `out` = output.** Every attempt
  counts toward the run total; the **unusable** slice (timeout / infra / judge
  error) is broken out separately so wasted spend is visible without distorting
  the per-usable-attempt average.
- All usage fields are **optional** — a backend that can't report them leaves
  them `null` and the line renders `—`. Support: `claude-code`, `codex`, `pi`,
  and `hermes` all report token usage; `codex` uses OpenAI semantics (its
  `input_tokens` includes cache) so it is normalized to the non-cached contract.
- **Dollar cost is deliberately not tracked** — it is inconsistent across
  backends and would need a maintained price table. Tokens are the volume signal;
  a dollar figure can be derived downstream if needed.
- `report --format json` includes a derived `usage_totals` block; the saved
  results JSON keeps only the raw per-attempt `usage` (totals are derived, never
  persisted). `compare` surfaces token/wall deltas — see above.

---

## Project layout

```text
caliper/
  commands/       CLI command implementations
  harness/        Agent execution backends (Claude Code, Codex, pi, Hermes)
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

Contributions are welcome. See [`CONTRIBUTING.md`](.github/CONTRIBUTING.md) for good first areas, the pre-PR checklist, the ruff formatting convention and pinned version, and the one-time `pre-commit install` step.

---

## Troubleshooting

**`codex judge failed: model ... is not supported`**
The model name is not available to your Codex account. Use a model that `codex exec --model <name>` accepts.

**`codex CLI not found`**

```bash
npm install -g @openai/codex
```

**`claude` command not found**
Install and authenticate Claude Code, or switch the backend to `codex` or `pi`.

**A task passes only because of `assert:`**
When a task has only `assert:`, no LLM judge runs. Add `expect:` if you also want an LLM to evaluate the transcript.
