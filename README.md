# Caliper: Know if your agent skill actually works

[![PyPI](https://img.shields.io/pypi/v/caliper-eval.svg)](https://pypi.org/project/caliper-eval/)
[![Python](https://img.shields.io/pypi/pyversions/caliper-eval.svg)](https://pypi.org/project/caliper-eval/)
[![Skills](https://skills.sh/b/edonadei/caliper)](https://skills.sh/edonadei/caliper)

Caliper is a lightweight evaluation harness for agent skills. Write a short spec of what "good" looks like, run it, and get a **success rate** you can track. Works with the agent you already use: **Claude Code, Codex, Pi, or Hermes**. Caliper installs the skill where the agent looks for skills and lets the agent choose.

**Teach your agent to evaluate:**

```bash
npx skills@latest add edonadei/caliper
```

**Or run it yourself:**

```bash
# Run the evaluation.
caliper run commit-commands.eval.yaml --k 3

# The control subject: your skill is not there.
caliper run commit-commands.eval.yaml --k 3 --ablate commit-commands

# Compare the runs. Did your skill improve it?
caliper compare .caliper/results/commit-commands/<evaluation-run>.json .caliper/results/commit-commands/<ablated-run>.json
```

You write a spec, a YAML file describing what "working" means. Either hand-write it or have `/grill-skill` generate it for you. `--ablate` runs the same tasks with that skill *removed*, and `caliper compare` diffs the two runs task by task:

<!-- Terminal output of `caliper compare`, rendered to SVG so the box-drawing
     table stays aligned on every screen. Regenerate with:
       python docs/render_readme_samples.py -->
![caliper compare, without commit-commands vs full neighbourhood on commit-commands: both tasks go 33.3% to 100.0% (+66.7%); tokens 290K to 180K, wall 1m 1s to 42s](docs/assets/compare-ablation.svg)

---

Agent skills are hard to test. A skill that works on your machine, on this prompt, today, might fail tomorrow after a model update or a one-line prompt edit. Caliper makes reliability measurable: define what success looks like, run the skill repeatedly, and get a success rate you can track over time.

Use Caliper to answer questions like:

- Is my agent still working the same with this new model?
- Did my prompt edit improved the skill?
- Does my skill fire when it should, and stay quiet when it needs to not trigger?
- Is the skill worth the context? Or would the base agent pass without it?
- Does it still pass the workflows it passed last week?
- Which agent (Claude Code, Codex, Pi, or Hermes) runs this skill more reliably?

---

## Quick start

### Path A: Agentic (let your agent drive)

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
/evaluate-skill run my-skill.eval.yaml --k 3
```

Browse past runs:

```text
/evaluate-skill list
/evaluate-skill report my-skill
```

### Path B: CLI (run it yourself)

**1. Install the CLI**

```bash
pipx install caliper-eval   # requires Python 3.10+
```

**2. Write a spec**

```yaml
# commit-writer.eval.yaml
skills:
  - ./SKILL.md                     # the skill under test
  - ../changelog-writer/SKILL.md   # a neighbour it might steal work from

tasks:
  # Autorater: the LLM judge reads the transcript and decides
  - name: Writes a conventional commit message
    prompt: "Summarize the staged git diff as a commit message."
    expect: >
      The response is a conventional-commit message: a concise subject
      line under 72 characters, followed by a body explaining why the
      change was made, not just what changed.
    activates: [commit-writer]

  # Script execution: a deterministic Python assertion
  - name: Keeps the subject line under 72 characters
    prompt: "Commit the staged changes."
    assert: |
      import subprocess
      subject = subprocess.run(
          ["git", "log", "-1", "--pretty=%s"], capture_output=True, text=True
      ).stdout.strip()
      assert len(subject) <= 72, f"subject line is {len(subject)} chars"
    activates: [commit-writer]

  # Activation: this prompt belongs to the neighbour, not to you
  - name: A release summary belongs to changelog-writer
    prompt: "What changed since v2.1? I need it for the release notes."
    activates: [changelog-writer]
```

Three kinds of check, and a task needs at least one. `expect:` is graded by the
judge LLM; `assert:` runs locally as Python; `activates:` asserts which skills
the agent chose to load. Use any combination.

The third task is the one you cannot write any other way. Both skills read git
history, so a release-notes request is exactly where `commit-writer` might grab
work that belongs to `changelog-writer`. Declaring the neighbour and asserting
`activates: [changelog-writer]` is how you find out. A task like that needs no
`expect:` at all: it skips the judge, so it costs a fraction of a graded task.

Caliper never pastes your skill into the prompt. It **installs** it where the
agent looks for skills and lets the agent decide, so a run measures the
`description` (does it fire?) and the body (does it work?) together, and
`activates:` is what tells the two apart.

The spec never names an engine. The skill and judge default to `claude-code`, and you pick a different agent/model at run time with `--model` / `--judge-model` (see [Choosing an engine](#choosing-an-engine)).

**3. Run it**

```bash
caliper run my-skill.eval.yaml --k 3          # --ablate <skill> for a run to diff against
```

**4. Read the output**

![caliper run of commit-writer at k=3. Three rows: 'Writes a conventional commit message' passes 3/3 (100.0%, 80K tokens) with a green tick in the act column; 'Keeps the subject line under 72 characters' 2/3 (66.7%, PARTIAL, 84K tokens) with a green tick; 'A release summary belongs to changelog-writer' shows no execution score, a red cross in the act column, and reads 'trigger only'. Score 83.3% over 2 tasks scored. Activation 77.8% over 3 asserted tasks. A per-skill table shows, for each skill, how many of the 9 attempts wanted it and how often it fired: commit-writer was wanted on 6 of 9, fired on 6/6 of those (100.0%) but also on 2/3 of the attempts that did not want it (66.7%); changelog-writer was wanted on 3 of 9, fired on only 1/3 (33.3%), and never fired unwanted (0/6, 0.0%). commit-writer is taking prompts that belong to changelog-writer. Failure panels below show the assertion error and the attempts where commit-writer activated on the changelog prompt](docs/assets/run-output.svg)

The report ends with the per-task failure panels: for each attempt that didn't pass, the output plus the assertion or autorater reason *why*. Full results are also saved as JSON under `.caliper/results/<spec>/` for you to inspect or `caliper compare` later. `--verbose` adds `pass@k` and `pass^k` columns (both derived from the raw rate) and a panel for every task.

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
  Harness  ──── runs your skill against the agent (Claude Code / Codex / Pi / Hermes)
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

### `evaluate-skill`: run and manage evals

Create, validate, run, and summarize evals from inside your normal workflow, with no separate terminal needed. The skill installs Caliper automatically if it's missing.

Then use it in Claude Code:

```text
/evaluate-skill run my-skill.eval.yaml --k 3
/evaluate-skill validate my-skill.eval.yaml
```

Or in Codex:

```text
Use the evaluate-skill skill to run my-skill.eval.yaml with k=3 and summarize the result.
```

### `grill-skill`: create evals interactively

Don't have evals yet? `grill-skill` guides you through creating them. It reads your `SKILL.md`, interviews you about what good behavior looks like, and generates a 3-task spec (happy path, edge case, adversarial). Then it runs the eval and loops: k=1 to validate, k=3 to measure, an ablated run to diff against before you commit.

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
| **Spec** | A `.eval.yaml` file that describes the skills, judge, and tasks to run |
| **Backend** | The CLI agent that executes the skill (`claude-code`, `codex`, `pi`, `hermes`) |
| **Judge** | What decides pass/fail: an LLM reading the transcript (`expect:`), Python assertions (`assert:`), or both |
| **success rate** | The primary score: run k times, measure how often a single run works (`pass@k`/`pass^k` are secondary views, under `--verbose`) |
| **Neighbourhood** | The set of skills a spec declares (`skills:`). All installed, none preloaded, and all assertable. This is the competition your `description` has to win |
| **Activation** | The agent *choosing* to load a skill. Asserted with `activates:` and scored on its own scoreboard, separate from the success rate |
| **Ablation** | Re-run the same tasks with a declared skill *removed* (`--ablate`), to prove the skill is doing the work. Name every skill for the bare agent. It's a property of the tasks, so run it once and keep re-diffing against it |
| **Attempt** | One isolated run of a single task (fresh temporary home, no session history) |

---

## Choosing an engine

The engine (backend + model) is a **runtime axis, not a spec field**. The spec
describes *what* is tested and *how* success is judged, and you pick the agent
that runs and grades it at invocation. Both default to `claude-code`; select a
different one with `--model` / `--judge-model`:

```bash
caliper run my-skill.eval.yaml                          # claude-code (default)
caliper run my-skill.eval.yaml --model codex            # codex, its default model
caliper run my-skill.eval.yaml --model codex:gpt-5.6-sol
caliper run my-skill.eval.yaml --model pi --judge-model claude-code
```

| Backend | Requires | Best for |
|---|---|---|
| `claude-code` | Claude Code CLI installed and authenticated | Testing Claude Code slash-command skills |
| `codex` | Codex CLI installed (`npm install -g @openai/codex`) | Testing Codex skills |
| `pi` | pi CLI installed (`npm install -g @earendil-works/pi-coding-agent`) and authenticated | Testing pi skills (agentskills.io) |
| `hermes` | Hermes Agent CLI installed and authenticated (Nous Research) | Testing skills on Hermes; `hermes:<provider>/<model>` selects the model |

Caliper runs skills only through CLI agents, so every backend can actually load and run a skill. There is no direct-API backend: to run against API-priced billing, configure one of these CLIs with an API key (e.g. `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) rather than selecting a separate backend.

The skill engine and judge engine are independent: you can test a Codex skill with a Claude judge, or any other combination, by pairing `--model` with `--judge-model`.

### Claude Code setup

Install and authenticate the `claude` CLI. `--model claude-code` uses your existing Claude Code auth, with no extra configuration needed.

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

`--model pi` runs `pi --print --mode json` and installs the declared skills under its agent dir, where pi discovers them (its `--skill` flag *preloads*, which caliper never does; pi's own `--no-skills` exists because discovery is the default). It reuses your `~/.pi/agent` auth and settings; the `:model` half of `--model pi:<model>` overrides pi's configured default when set. Set `PI_CLI_PATH` to force a specific binary. Note: pi's built-in default provider is `google`, so running `--model pi` with no model relies on your pi config to resolve a provider you are authenticated for.

### Hermes setup

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes login   # authenticate
hermes model   # pick a default model/provider you have credits for
```

Hermes is a stateful, always-on agent (persistent memory, a persona, auto-generated skills), so Caliper **normalizes it to a neutral agent** to keep its score apples-to-apples with the other backends: every attempt runs in an isolated `HERMES_HOME` seeded with your `~/.hermes` auth/config only (never `SOUL.md`/`MEMORY.md`), with `--ignore-rules` and `--yolo` (so an approval prompt can't hang the non-interactive oneshot), and only the spec's declared skills are installed (its `--skills` flag is documented as *preload*, so caliper does not pass it). `--model hermes` runs `hermes -z` (oneshot) then `hermes sessions export` to recover the full tool-call trajectory; `--model hermes:<provider>/<model>` (e.g. `hermes:anthropic/claude-opus-4-8`) selects the model, otherwise your `~/.hermes/config.yaml` default is used. Point it at a provider you have credits for. If a run fails because no model is selected or a provider login lapsed, Caliper tells you to run `hermes model`. Set `HERMES_CLI_PATH` to force a specific binary. Hermes updates itself (`hermes update`), so it is not part of `caliper update-cli`.

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
5. Run once with `--ablate <skill>` and `caliper compare` the two runs, to prove the skill is making a difference. That arm is a property of the *tasks*, so keep it and re-diff against it as the skill changes.
6. Commit the spec alongside the skill so contributors can run the same eval.

```text
/evaluate-skill run my-skill.eval.yaml --k 3 --verbose
```

---

## Spec format

To scaffold a spec, use the [`evaluate-skill`](#evaluate-skill-run-and-manage-evals)
or [`grill-skill`](#grill-skill-create-evals-interactively) skill, or hand-write
the YAML below.

```yaml
skills:                         # installed where the agent looks for skills,
  - ./SKILL.md                  #   never pasted into the prompt
  - ../evaluate-skill/SKILL.md  # a path source: whatever that file says today
  - repo: vercel-labs/agent-skills   # a git source: caliper clones it
    ref: a1b2c3d                     #   optional — omit to track the default branch
    path: skills/tdd/SKILL.md        #   optional — defaults to SKILL.md at the root
                                # omit `skills:` entirely for a bare agent

# Note: there is no `backend`/`model` or `judge:` block. The engine is a runtime
# axis: pass `--model` / `--judge-model` at run time (default: claude-code).

sandbox:
  extra_path:
    - ./bin                     # prepended to PATH inside each attempt
  forbidden_files:
    - ".*\\.eval\\.yaml$"       # prevents agent from reading the spec
    - "./.caliper/.*"           # prevents agent from reading saved results

mcp:                            # optional: MCP servers the agent may use
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

  - name: A neighbour's prompt: yours must not hijack it
    prompt: "How reliable is my commit-message skill? Run it 10 times."
    activates: [evaluate-skill]   # exactly these skills, and no others

  - name: Unrelated work, silence expected
    prompt: "Rename `resolved_model` to `engine_model` across the repo."
    activates: []                 # nothing should fire
```

Each task needs at least one of `expect`, `assert` or `activates`. Task IDs are assigned automatically as `task-001`, `task-002`, and so on.

> **Upgrading an existing spec?** `skill:` became `skills:` in v0.10. See [docs/MIGRATING-to-skills.md](docs/MIGRATING-to-skills.md) for a short checklist, including the two traps a find-and-replace misses (stale `skill.path` inside `prompt:`/`expect:`/`assert:` strings, and prompts that name the skill they're testing).

### `skills:`, the neighbourhood

Every entry is installed at the agent's own skills root under its frontmatter
`name:`, and **nothing is preloaded**. Entries are peers: no entry is "the skill
under test", so `activates:` always names skills explicitly.

The set is closed. The agent sees these skills and nothing else, which is what
makes activation a measurement rather than a guess. It also means a skill you
*don't* declare can never activate: if yours delegates to another skill, declare
that one too and enumerate the whole chain (`activates: [mine, helper]`), which
makes "did it actually delegate?" assertable.

A skill must be a `SKILL.md` in a directory, carrying frontmatter `name:` and
`description:`. A lone slash-command `.md` is rejected: with no name and no
description there is nothing for an agent to discover.

#### Path sources and git sources

An entry is written one of two ways, and the shape is the difference:

| Entry | Means |
|---|---|
| `- ./SKILL.md` | a **path source** — a file on your disk, whatever it says at run time |
| `- {repo: …, ref: …, path: …}` | a **git source** — caliper clones it and resolves `ref:` to a commit |

Git sources are how you give your `description` real competition to win against
without vendoring somebody's repo into yours. One entry is one skill; entries
sharing a repo and commit share one clone, so naming five skills from a pack
costs five entries and one fetch.

`repo:` takes anything git can clone. A bare `owner/name` is expanded to
`https://github.com/owner/name`; a URL, an `scp`-style `git@host:owner/name`, or
a filesystem path is passed through untouched. To point at a *local* repo by
relative path, write `./owner/name` — the leading `./` is what tells it apart
from the shorthand.

`ref:` is optional and an omitted one tracks the default branch, so it *will*
move. That's allowed rather than forbidden because caliper records the commit it
resolved and `compare` tells you when it moved — see below. Pinning a commit is
still worth it: a pinned entry is fully offline once fetched, an unpinned one
costs one `git ls-remote` per run.

`caliper run` fetches before the first attempt, so a bad `repo:` costs you
nothing. `caliper validate` never touches the network: it resolves git sources
from the cache when it can and reports the rest as *not cached* (and says so
when that means it couldn't check your `activates:` names).

Checkouts land in `~/.cache/caliper/skills/` (or `$XDG_CACHE_HOME/caliper/…`),
keyed by resolved commit — so they're immutable, shared across every spec that
names them, and safe to delete. Set `CALIPER_CACHE_DIR` to put them elsewhere.

If a git source can't be fetched and isn't cached, the run **refuses** — a
member silently missing would measure your skill against competition that
wasn't there. If it's cached but the remote is unreachable, the run uses the
cache and says so.

#### Skill drift

`caliper compare` reports any member whose text changed between the two runs.
A **git source** that moved gets a warning: the spec said where its bytes came
from, and the delta you're reading is confounded. A **path source** that moved
is shown without alarm — that's usually the edit the run exists to measure.

```
 ⚠ tdd changed between runs — git source, a1b2c3d → e4f5g6h; pin `ref:` to hold it fixed
   my-skill changed between runs — path, 4fc7951 → bcbcbde
```

This is a change in *text* at constant membership. A change in *membership* —
different skills installed — is the separate neighbourhood warning.

### `activates:`: did the agent reach for it?

`activates:` asserts the **exact set** of skills that loaded on each attempt.

| Form | Means |
|---|---|
| *(omitted)* | not asserted; the column still shows what loaded, dimmed |
| `activates: [a]` | exactly `a` fired, and nothing else |
| `activates: [a, b]` | both fired, which is how a delegating skill asserts its chain |
| `activates: []` | nothing fired; silence held |

A task with `activates:` and no `expect:`/`assert:` is a **trigger probe**: it
asks only what the agent reached for, skips the judge entirely (so it is much
cheaper than an execution task), and reports as `trigger only` rather than a
zero. Use it for neighbour and silence probes, where there is no work worth
grading.

Activation is scored on its **own scoreboard**, never blended into the success
rate. A failing `description` and a failing body are fixed in different places,
so one number mixing them would point at neither.

### MCP servers (`mcp:`)

The optional `mcp:` block declares the [MCP](https://modelcontextprotocol.io) servers the agent-under-test may use. It is a capability granted to the agent for the eval, part of the run environment like `sandbox:`, so it lives in the spec rather than behind a flag. It is a top-level mapping keyed by server name (a sibling of `sandbox:` and `skills:`, and it applies whether or not the eval declares any skill). Each server's tools appear in the transcript as a namespaced call an `expect:` judge can verify (`mcp__<server>__<tool>` on `claude-code` and `codex`, `mcp_<server>_<tool>` on `hermes`), so word an `expect:` around the tool's behavior, not one backend's exact spelling, if the spec is meant to run under more than one engine.

A server is either **local (stdio)**, a `command` the harness spawns, or **remote (`type: http` or `sse`)**, a hosted endpoint at `url`, the shape most connectors (Google Drive, Notion, and so on) use:

```yaml
mcp:
  weather:                      # local stdio server (the default transport)
    command: python3            # required: the local stdio command to spawn
    args: [./servers/weather.py]  # optional
    env:                        # optional
      API_TOKEN: ${MCP_API_TOKEN}
  gdrive:                       # remote server
    type: http                  # required for remote: http or sse
    url: https://mcp.example.com/gdrive   # required for remote
    headers:                    # optional: usually auth
      Authorization: Bearer ${GDRIVE_TOKEN}
```

- **`claude-code`, `hermes`, and `codex`.** All three wire `mcp:` through: `claude-code` honors stdio and remote (HTTP/SSE); `hermes` honors stdio and remote **header-auth** (it translates the block into its native `mcp_servers` config inside the isolated `HERMES_HOME`, resolving `${VAR}` at the harness boundary and overwriting any of your personal servers so an attempt sees only the declared set); `codex` honors stdio and remote **header-auth** the same way, translating the block into `[mcp_servers.*]` tables in the isolated `~/.codex/config.toml` (stdio as `command`/`args`/`env`, remote as `url` + a static `http_headers` map of boundary-resolved literals; codex infers its one streamable-HTTP transport from `url`, so `http`/`sse` collapse onto it), resolving `${VAR}` at the boundary and replacing any personal servers from your real config so an attempt sees only the declared set. Remote **OAuth** is not supported on `hermes` or `codex`, since it needs an interactive browser flow the harness can't drive. Running a spec that declares `mcp:` on a backend that can't honor it is a hard error rather than a silent no-op. `pi` does **not** and **will not** honor `mcp:` natively: its agent has no MCP by design. Instead of MCP, expose the capability as a CLI tool your skill drives (a skill with a README) or a pi extension, or run the eval on `claude-code`/`hermes`/`codex`. Running an `mcp:` spec on `pi` fails with that guidance.
- **Transport is set by `type:`.** Omitted (or `stdio`) means a local `command`; `http`/`sse` means a remote `url`. The two field sets are mutually exclusive: a stdio server can't set `url`/`headers`, and a remote server can't set `command`/`args`/`env`.
- **Secrets stay out of the spec.** A value in a stdio `env:`, a remote `headers:`, or a remote `url:` may reference a host environment variable as `${VAR}`; it is resolved from your shell at run time (never written into the committed spec), and an unset variable fails the run with a clear message.
- **Server names** must match `[A-Za-z0-9_-]+` so the backend's namespaced tool handle (`mcp__<server>__<tool>` / `mcp_<server>_<tool>`) is well-formed.

`caliper validate` checks the `mcp:` block and reports a malformed entry (bad name, unknown key, unknown `type`, a stdio server missing/blank `command`, or a remote server missing `url`).

---

## Judging

### LLM autorater (`expect:`)

The judge engine reads the full attempt transcript and decides whether the `expect` condition was met. When the backend captures tool-call traces (Claude Code, Codex, pi, Hermes), those traces are included, so the judge can verify things like "the agent used tool X" without relying on the final text alone.

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
| `caliper list [spec]` | List specs and saved runs. Per-spec, each row carries its **Run** id and which skills that run **ablated** — how you find the control arm to diff against |
| `caliper report <spec-or-result>` | Re-render saved results |
| `caliper compare <A> <B>` | Diff two saved runs of the same eval, task by task. Each side is a spec name (that spec's **latest** run) or a results-JSON path; they must be two distinct runs |
| `caliper update-cli [backend]` | Check or update installed agent CLI versions |

### `caliper run` flags

| Flag | Default | Description |
|---|---|---|
| `--k INT` | `3` | Attempts per task |
| `--ablate NAME` | none | Run without this declared skill installed (repeatable; name them all for the bare agent) |
| `--workers INT` | `4` | Parallel task workers |
| `--timeout INT` | `120` | Seconds per attempt |
| `--fail-fast INT` | `0` | Stop a task after N consecutive `infra_error`/`timeout` attempts (`0` disables) |
| `--model TARGET` | `claude-code` | Skill engine: backend and/or model (see below) |
| `--judge-model TARGET` | `claude-code` | Judge engine: backend and/or model (see below) |
| `--verbose` | off | Show per-attempt judge reasoning |
| `--output PATH` | — | Also save results JSON to a specific path |

#### `--model` and `--judge-model` syntax

The engine is not stored in the spec; these flags select it, defaulting to `claude-code` when omitted. Both accept a `backend:model` compound value, a bare backend name, or a bare model name:

```bash
# Backend and model together
caliper run my-skill.eval.yaml --model codex:gpt-5.6-sol

# Backend only (that backend's default model)
caliper run my-skill.eval.yaml --model codex

# Model only (backend stays claude-code)
caliper run my-skill.eval.yaml --model claude-fable-5

# Select the judge engine independently
caliper run my-skill.eval.yaml --model codex --judge-model claude-code:claude-haiku-4-5-20251001
```

Accepted backends: `claude-code`, `codex`, `pi`, `hermes` (alias: `claude` → `claude-code`). The actual engine used is recorded in each saved run's `RunMeta` (the skill `backend`/`model`, and the `judge_backend`/`judge_model` that graded it), so results stay traceable even though the spec doesn't pin it. When you don't name a model and the CLI uses its own default, `RunMeta` records the concrete model the agent resolved rather than a bare "default", wherever the backend reports it: the skill model from hermes' session export, and the `judge_model` from the `claude-code` judge's JSON output. `judge_model` stays empty for an `assert:`-only run, where no LLM judge fired. When `--judge-model` is omitted, the claude-code judge still pins `claude-sonnet-5` at execution time so it does not inherit a stale model from the installed Claude CLI; that pin is not written into `RunMeta` unless you pass it explicitly or the autorater reports what it used.

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

<!-- Terminal output of `caliper compare`, rendered to SVG so the box-drawing
     table stays aligned on every screen. Regenerate with:
       python docs/render_readme_samples.py -->
![caliper compare of two commit-simple runs: commits cleanly holds at 100%, handles conflict regresses 100.0% to 20.0% (-80.0%), pushes upstream becomes unmeasured; 1 regression, 1 unmeasured, and unmatched tasks on each side](docs/assets/compare-runs.svg)

How the diff reads:

- **Each row reads `before → after`.** The runs are named once in the header
  (an ablation pair is titled `without <skill> → full neighbourhood`), so there's
  no A/B legend.
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
regression flags, unmatched lists, warnings, `skill_drift`, and per-side usage)
for scripting. Each `skill_drift` entry carries the member's `name`,
`source_kind`, and the two sides' `a_ref`/`b_ref` — so a script sees drift for
*every* member, including the path-sourced ones that don't raise a warning.

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
| `not_checked` | the task authored no `expect:`/`assert:`, so it is a trigger probe | ⊘ not asked |

`not_checked` is the one outcome that is neither: it leaves the denominator like
an unusable attempt, but nothing went wrong, so it is never reported as an error
and its tokens are not counted as wasted spend.

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
attempts. To get a delta against the bare agent, run the same tasks with
`--ablate` and `caliper compare` the two saved runs.

`--fail-fast N` stops scheduling new attempts for a task after N consecutive
`infra_error` or `timeout` outcomes (default `0` runs all k). An early-stopped
task shows as `ABORTED`; if every completed attempt was unusable, its `score`
stays `null` and it's skipped in the aggregate.

---

## Token and time usage

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
- Each `AttemptRecord` also carries an optional `transcript` array of ordered
  turns (`role`, `content`, and tool `tool_name`/`tool_input`/`tool_output` when
  present). This preserves the full tool-call trace in saved results for later
  inspection; older JSON without the field still loads (`transcript` is `null`).
- Each `SkillSnapshot` records `source_kind` (`"path"` or `"git"`) alongside
  `git_repo`/`git_sha`, so a saved run says how each member of the neighbourhood
  was obtained and — for a git source — the exact commit it was fetched at.
  Older JSON without the field still loads and reads as `"path"`.
- In the summary, **`in` = input + cache_read + cache_creation** and **`out` =
  output**. The **unusable** slice (timeout / infra / judge error) is broken out
  separately, so wasted spend stays visible without distorting the per-attempt
  average.
- **Support:** `claude-code`, `codex`, `pi`, and `hermes` all report usage; a
  backend that can't leaves the fields `null` and renders `—`. `codex` includes
  cache in its `input_tokens`, so it's normalized to the non-cached contract above.
- **Dollar cost is deliberately not tracked**: it's inconsistent across backends.
  Tokens are the volume signal, so derive a dollar figure downstream if you need one.
- **An ablated run is an ordinary saved run**, so the skill-vs-bare-agent view is
  `caliper compare` like any other diff — same table, attempt strips, and
  token/wall deltas.
- `report --format json` adds a derived `usage_totals` block; the saved JSON keeps
  the raw per-attempt `usage` (totals are always derived, never persisted).

### Activation fields in saved results

- Each `AttemptRecord` carries `activated`, the skills the agent chose to load,
  recorded on every attempt whether or not the task asserted on it. It is
  `null` when nothing was *observable*: an attempt with the whole neighbourhood
  ablated (no skills installed), or a timeout / infra failure whose transcript
  may be truncated. A
  bare `[]` in those cases would be a fabricated "the description never fired",
  so caliper never writes one.
- `activation_passed` is the verdict: `null` = **not asserted** (a different
  `null` from `activated`'s, matching the existing `assert_passed` idiom).
- `TaskResult` carries `activation_expected` (the task's `activates:` set) plus
  derived `activation_usable` / `activation_successes` / `activation_score`.
- `AggregateScore` carries `avg_activation_score`, `activation_tasks`, and
  `activation_per_skill` (per-skill `expected`/`fired`/`hits` with derived
  `recall`/`precision`), alongside `scored_tasks` for the execution half.
- `RunMeta.era` records the loading discipline a run was produced under.
  Pre-#18 runs have no era, and **`caliper compare` refuses** to diff across
  that boundary, because those runs measured something else (see
  [ADR 0013](docs/adr/0013-install-and-discover-is-the-only-loading-discipline.md)).
  A *neighbourhood* change between two same-era runs only warns.
- `RunResults.skill_snapshots` is a list, one snapshot per declared skill,
  since a neighbour's `description` is part of what produced the score. Runs
  saved before #18 carry a singular `skill_snapshot`; they still load, and their
  missing era is what makes `compare` refuse them.
- `TaskComparison` carries `a_activation`/`b_activation`/`activation_delta`/
  `activation_regression`, and `RunComparison` carries
  `has_activation_regression`, kept strictly separate from `has_regression`.

---

## Contributing

Contributions are welcome. See [`CONTRIBUTING.md`](.github/CONTRIBUTING.md) for good first areas, the pre-PR checklist, the ruff formatting convention and pinned version, and the one-time `pre-commit install` step.

---

## Troubleshooting

**`codex judge failed: model ... is not supported`**
The model name is not available to your Codex account. Use a model that `codex exec --model <name>` accepts.

**`Judge model ... is unavailable` / `Judge authentication failed` / `Judge rate limited`**
The judge CLI reached the provider and the call was refused. Caliper classifies these at the harness boundary (from the CLI's structured output) and suggests passing `--judge-model <backend[:model]>` to pick an available judge engine or model. Example: `caliper run my-skill.eval.yaml --judge-model claude-code:claude-haiku-4-5-20251001`.

**A task passes only because of `assert:`**
When a task has only `assert:`, no LLM judge runs. Add `expect:` if you also want an LLM to evaluate the transcript.
