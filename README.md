# Caliper — Reliability testing for agent skills

[![PyPI](https://img.shields.io/pypi/v/caliper-eval.svg)](https://pypi.org/project/caliper-eval/)
[![Python](https://img.shields.io/pypi/pyversions/caliper-eval.svg)](https://pypi.org/project/caliper-eval/)
[![Skills](https://skills.sh/b/edonadei/caliper)](https://skills.sh/edonadei/caliper)

Know whether your skill actually works. Write a short spec of what "good" looks like, run it _k_ times, and get a **success rate** you can track. Caliper also runs the tasks without the skill, so you can see whether it's the skill or the base agent doing the work. Works with the agent you already use: Claude Code, Codex, Pi, or Hermes.

**Teach your agent to evaluate:**

```bash
npx skills@latest add edonadei/caliper
```

**Or run it yourself:**

```bash
caliper run commit-commands.eval.yaml --k 3 --baseline
```

You write a spec — a few lines of YAML describing what "working" means, which you hand-write or have `/grill-skill` generate for you. With `--baseline`, Caliper runs each task with and without the skill and diffs the two runs task by task:

**CALIPER — compare — `commit-commands`** · `no skill → with skill` · k=3

| Task | success | Δ | attempts |
|---|---:|---:|---|
| Commits a new feature | 33.3% → 100.0% | +66.7% | ✓✗✗ → ✓✓✓ |
| Commits a bug fix | 33.3% → 100.0% | +66.7% | ✗✓✗ → ✓✓✓ |

**Overall** 33.3% → 100.0% · Δ (matched) **+66.7% ↑** · **Tokens** 290K → 180K (−38%, −110K) · **Wall** 1m 1s → 42s (−31%, −19s)

---

Agent skills are hard to test. A skill that works on your machine, on this prompt, today, might fail tomorrow after a model update or a one-line prompt edit. Caliper makes reliability measurable: define what success looks like, run the skill repeatedly, and get a success rate you can track over time.

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
caliper run my-skill.eval.yaml --k 3          # add --baseline to diff vs the bare agent
```

**4. Read the output**

```text
─────────── CALIPER  —  my-skill  (claude-code)  —  2026-06-19 14:23 ───────────

╭──────────────────────────────────┬───────┬─────────┬────────┬──────┬───────────╮
│ Task                             │ k (3) │ success │ Tokens │ Wall │           │
├──────────────────────────────────┼───────┼─────────┼────────┼──────┼───────────┤
│ Writes a conventional commit msg │  3/3  │  100.0% │    79K │  27s │  ✓ PASS   │
│ Generates a valid config file    │  2/3  │   66.7% │    82K │  33s │ ~ PARTIAL │
╰──────────────────────────────────┴───────┴─────────┴────────┴──────┴───────────╯

 Score   83.3%  █████████████████░░░

 Tokens   159K in / 2K out
 Wall     1m 0s  10.0s per attempt

Results saved to .caliper/results/my-skill/2026-06-19T14-23-01Z.json
```

`--verbose` adds `pass@k` and `pass^k` columns (both derived from the raw rate).

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
  success rate + saved transcript
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
| **success rate** | The primary score: run k times, measure how often a single run works (`pass@k`/`pass^k` are secondary views, under `--verbose`) |
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
hermes login   # authenticate
hermes model   # pick a default model/provider you have credits for
```

Hermes is a stateful, always-on agent (persistent memory, a persona, auto-generated skills), so Caliper **normalizes it to a neutral agent** to keep its score apples-to-apples with the other backends: every attempt runs in an isolated `HERMES_HOME` seeded with your `~/.hermes` auth/config only (never `SOUL.md`/`MEMORY.md`), with `--ignore-rules` and `--yolo` (so an approval prompt can't hang the non-interactive oneshot), and the skill-under-test is staged as the sole local skill. `--model hermes` runs `hermes -z` (oneshot) then `hermes sessions export` to recover the full tool-call trajectory; `--model hermes:<provider>/<model>` (e.g. `hermes:anthropic/claude-sonnet-4.6`) selects the model, otherwise your `~/.hermes/config.yaml` default is used — point it at a provider you have credits for. If a run fails because no model is selected or a provider login lapsed, Caliper tells you to run `hermes model`. Set `HERMES_CLI_PATH` to force a specific binary. Hermes updates itself (`hermes update`), so it is not part of `caliper update-cli`.

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

mcp:                            # optional — MCP servers the agent may use
  weather:                      # server name → a mcp__weather__<tool> call in the transcript
    command: python3            # a local stdio server the harness spawns
    args: [./servers/weather.py]
    env:
      API_TOKEN: ${MCP_API_TOKEN}   # ${VAR} resolves from your shell at run time
  gdrive:                       # a remote (hosted) server reached over HTTP
    type: http                  # http or sse
    url: https://mcp.example.com/gdrive
    headers:
      Authorization: Bearer ${GDRIVE_TOKEN}   # ${VAR} resolves at run time

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

### MCP servers (`mcp:`)

The optional `mcp:` block declares the [MCP](https://modelcontextprotocol.io) servers the agent-under-test may use — a capability granted to the agent for the eval, part of the run environment like `sandbox:`, so it lives in the spec rather than behind a flag. It is a top-level mapping keyed by server name (a sibling of `sandbox:`, not nested under `skill:` — it applies whether or not the eval uses a skill). Each server's tools appear in the transcript as a namespaced call an `expect:` judge can verify — `mcp__<server>__<tool>` on `claude-code` and `codex`, `mcp_<server>_<tool>` on `hermes` — so word an `expect:` around the tool's behaviour, not one backend's exact spelling, if the spec is meant to run under more than one engine.

A server is either **local (stdio)** — a `command` the harness spawns — or **remote (`type: http` or `sse`)** — a hosted endpoint at `url`, the shape most connectors (Google Drive, Notion, …) use:

```yaml
mcp:
  weather:                      # local stdio server (the default transport)
    command: python3            # required — the local stdio command to spawn
    args: [./servers/weather.py]  # optional
    env:                        # optional
      API_TOKEN: ${MCP_API_TOKEN}
  gdrive:                       # remote server
    type: http                  # required for remote — http or sse
    url: https://mcp.example.com/gdrive   # required for remote
    headers:                    # optional — usually auth
      Authorization: Bearer ${GDRIVE_TOKEN}
```

- **`claude-code`, `hermes`, and `codex`.** All three wire `mcp:` through: `claude-code` honors stdio and remote (HTTP/SSE); `hermes` honors stdio and remote **header-auth** (it translates the block into its native `mcp_servers` config inside the isolated `HERMES_HOME`, resolving `${VAR}` at the harness boundary and overwriting any of your personal servers so an attempt sees only the declared set); `codex` honors stdio and remote **header-auth** the same way — it translates the block into `[mcp_servers.*]` tables in the isolated `~/.codex/config.toml` (stdio as `command`/`args`/`env`, remote as `url` + a static `http_headers` map of boundary-resolved literals; codex infers its one streamable-HTTP transport from `url`, so `http`/`sse` collapse onto it), resolving `${VAR}` at the boundary and replacing any personal servers from your real config so an attempt sees only the declared set. Remote **OAuth** is not supported on `hermes` or `codex` — it needs an interactive browser flow the harness can't drive. Running a spec that declares `mcp:` on a backend that can't honor it is a hard error rather than a silent no-op. `pi` does **not** and **will not** honor `mcp:` natively — its agent has no MCP by design; instead of MCP, expose the capability as a CLI tool your skill drives (a skill with a README) or a pi extension, or run the eval on `claude-code`/`hermes`/`codex`. Running an `mcp:` spec on `pi` fails with that guidance.
- **Transport is set by `type:`** — omitted (or `stdio`) means a local `command`; `http`/`sse` means a remote `url`. The two field sets are mutually exclusive: a stdio server can't set `url`/`headers`, and a remote server can't set `command`/`args`/`env`.
- **Secrets stay out of the spec.** A value in a stdio `env:`, a remote `headers:`, or a remote `url:` may reference a host environment variable as `${VAR}`; it is resolved from your shell at run time (never written into the committed spec), and an unset variable fails the run with a clear message.
- **Server names** must match `[A-Za-z0-9_-]+` so the backend's namespaced tool handle (`mcp__<server>__<tool>` / `mcp_<server>_<tool>`) is well-formed.

`caliper validate` checks the `mcp:` block and reports a malformed entry (bad name, unknown key, unknown `type`, a stdio server missing/blank `command`, or a remote server missing `url`).

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

An **ablation** compares two runs of the *same* eval: a full skill against a
shortened variant, or the same skill at two points in time. `caliper compare
<A> <B>` diffs two already-saved runs task by task, so you don't have to
hand-write a JSON script to answer "did this change regress?".

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
name (which resolves to its latest run) or a path to a results JSON. There are
no `--run-a/-b` flags. To pin a historical run, name its JSON path.

**CALIPER — compare — `commit-simple`** · 2026-07-01T10-00-00Z (claude-code) → 2026-07-02T09-00-00Z (claude-code) · k=5

| Task | success | Δ | attempts |
|---|---:|---:|---|
| commits cleanly | 100.0% → 100.0% | — | ✓✓✓✓✓ → ✓✓✓✓✓ |
| handles conflict | 100.0% → 20.0% | -80.0% | ✓✓✓✓✓ → ✓✗✗✗✗ |
| pushes upstream | 80.0% → — | — | ✓✓✓✓✗ → ⊘⊘⊘⊘⊘ |

- **Overall** 100.0% → 60.0% · Δ (matched) **-40.0% ↓**
- **Tokens** 1.2M → 700K (−42%, −500K) · **Wall** 6m 18s → 3m 40s (−42%, −2m 38s)
- ⚠ 1 regression: handles conflict
- ⊘ 1 unmeasured (excluded from Δ): pushes upstream
- unmatched — only in A: flaky task · only in B: new task

How the diff reads:

- **Each row reads `before → after`.** The runs are named once in the header
  (with `--baseline`, `no skill → with skill`), so there's no A/B legend.
- **Tasks are matched by name**, so reordering doesn't matter. A task in only one
  run is listed as **unmatched** and left out of the delta.
- **`Δ` is `after − before`**, and the headline `Δ (matched)` averages only the
  tasks measured on both sides, so it stays strictly like-for-like. A negative Δ
  renders red and flags a **regression**.
- **Unusable attempts can't fake a loss.** A side with no usable attempts
  (rate-limit / timeout / judge error) shows `—` and never counts as a regression.
- **Token and wall-clock deltas are secondary** and never a regression: a drop is
  green (cheaper), a rise red (a trade-off to weigh). Only the score feeds
  `has_regression`.

`--format json` serializes the full comparison (per-task scores, deltas,
regression flags, unmatched lists, warnings, and per-side usage) for scripting.

---

## Scoring

Every attempt carries a typed **outcome**, so infrastructure and judge noise are
not scored as task failure:

| Outcome | Meaning | Counts toward the score? |
| --- | --- | --- |
| `pass` | satisfied the task's judge(s) | ✅ success |
| `task_fail` | the skill genuinely failed the task | ✅ attempt |
| `cheat` | a forbidden-file read was detected | ✅ attempt |
| `infra_error` | harness failure: nonzero exit, or a detected rate-limit / spending-cap | ❌ unusable |
| `timeout` | exceeded the time budget with no result | ❌ unusable |
| `judge_error` | the judge produced no verdict (unparseable / errored autorater) | ❌ unusable |

The primary metric is the **raw success rate**: how often a *single* run works,
computed over the **usable** attempts (the ones that got a fair shot). Unusable
attempts leave the denominator and are reported as a separate "N unusable" count:

```
usable  = pass + task_fail + cheat
score   = successes / usable                # raw rate; None if usable == 0
```

Two secondary views are kept for anyone who wants them (shown under `--verbose`,
and on every task in the JSON as `pass_at_k` / `pass_hat_k`):

```
pass@k  = 1 - (1 - score) ^ usable   # P(≥1 of k passes)
pass^k  = score ^ usable             # P(all k pass)
```

**Which one to look at** depends on how the skill is actually used:

| The question you're asking | Metric | For a `1/3` skill (k=3) |
| --- | --- | --- |
| How reliable is a **single** run? *(default)* | **success rate** | `33%` |
| If I **retry** up to k times and keep any win, do I get one? | `pass@k` | `70%` |
| Will it work on **every** run, no exceptions? | `pass^k` | `4%` |

Use **`pass@k`** when retrying is cheap and you keep the winning run; it's the
optimistic view, always **≥** the raw rate. Use **`pass^k`** when the skill runs
unattended and one failure breaks the chain; it's the strict view, always **≤**
the raw rate. Caliper leads with the raw rate because `pass@k` flatters flaky
skills (`1/3 → 70.4%`).

The aggregate is the average task success rate, skipping tasks with no usable
attempts. With `--baseline`, Caliper runs the same tasks without the skill and
reports the delta.

`--fail-fast N` stops scheduling new attempts for a task after N consecutive
`infra_error` or `timeout` outcomes (default `0` runs all k). An early-stopped
task shows as `ABORTED`; if every completed attempt was unusable, its `score`
stays `null` and it's skipped in the aggregate.

---

## Token & wall-clock usage

Pass@k tells you *whether* a skill works; usage tells you what it **costs** to
get there. Two runs can have identical scores while one burns twice the tokens.
Caliper records **token volume** and **wall-clock time** per attempt and rolls
them up per run:

```
 With skill    100.0%  ████████████████████

 Tokens   1.2M in / 340K out
 Wall     6m 18s  12.6s per attempt
 ⊘ unusable spend: 180K tokens, 42s  (2 attempts, not counted in the average)
```

- The results table carries per-task `Tokens` and `Wall` columns, so you can spot
  the expensive task at a glance; the summary line below aggregates the whole run.
- Each `AttemptRecord` carries an optional `usage` object that splits tokens four
  ways:
    - `input_tokens`: prompt, excluding cache
    - `output_tokens`: generated output
    - `cache_read_tokens`: cache hits
    - `cache_creation_tokens`: cache writes

  Those four are **disjoint**, so the computed `total_tokens` never
  double-counts. Wall-clock time comes from `duration_seconds`, which was already
  recorded.
- In the summary, **`in` = input + cache_read + cache_creation** and **`out` =
  output**. The **unusable** slice (timeout / infra / judge error) is broken out
  separately, so wasted spend stays visible without distorting the per-attempt
  average.
- **Support:** `claude-code`, `codex`, `pi`, and `hermes` all report usage; a
  backend that can't leaves the fields `null` and renders `—`. `codex` includes
  cache in its `input_tokens`, so it's normalized to the non-cached contract above.
- **Dollar cost is deliberately not tracked**: it's inconsistent across backends.
  Tokens are the volume signal, so derive a dollar figure downstream if you need one.
- **With `--baseline`**, the no-skill run is kept and the report renders as a
  `compare` view (same table, attempt strips, and token/wall deltas), showing the
  skill-vs-bare-agent difference side by side.
- `report --format json` adds a derived `usage_totals` block; the saved JSON keeps
  the raw per-attempt `usage` (totals are always derived, never persisted).

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
