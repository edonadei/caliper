# Caliper reference

`caliper <command> --help` lists every flag. This file covers what `--help`
doesn't: the spec format, how engines are chosen, what the results mean, and how
to design tasks.

## Commands

```bash
caliper validate spec.eval.yaml                   # offline check; exit 1 if invalid
caliper run spec.eval.yaml --k 3                  # attempts per task (default 3)
caliper run spec.eval.yaml --k 3 --ablate my-skill   # the control: that skill removed
caliper run spec.eval.yaml --ablate a --ablate mcp:srv --no-user-customizations  # every skill and server, isolated: the bare agent
caliper run spec.eval.yaml --no-user-customizations  # isolated: only the spec's mcp: servers
caliper run spec.eval.yaml --verbose              # per-attempt judge reasoning, pass@k, pass^k

caliper list                                      # every spec with its latest score
caliper list my-skill                             # one spec's runs, ablated ones marked
caliper report my-skill                           # latest run as a table
caliper report my-skill --run 2026-05-12T14-23-01Z --verbose
caliper report results.json --format json

caliper compare control.json my-skill             # Δ = b − a, task by task
caliper compare a.json b.json --format json
```

`report` and `compare` take a spec name (its latest run) or a results-JSON path.
Pin an older run by its path.

Other `run` controls: `--workers N` (parallel attempts across all tasks, default
4; more workers share one rate limit and risk more `infra_error`), `--timeout`
(seconds per attempt, default 120), `--fail-fast N` (stop a task after N
consecutive `infra_error`/`timeout` attempts).

## Spec format (.eval.yaml)

```yaml
skills:                     # the neighbourhood: every entry installed, none preloaded
  - ./SKILL.md              # path source, relative to the spec
  - repo: vercel-labs/agent-skills   # git source, cloned by caliper
    ref: a1b2c3d            # optional; omit to track the default branch
    path: skills/tdd/SKILL.md        # optional; defaults to SKILL.md at the root

sandbox:
  forbidden_files:          # extra regexes only: the spec itself and any
    - "./answers/.*"        #   .caliper/ results are forbidden already
  extra_path: ["./bin"]     # optional; relative to the spec, prefixed to PATH

user_customizations: false  # optional; isolate from the user's own skills and setup

mcp:                        # optional MCP servers the agent may use
  weather:                  # local stdio server
    command: python3
    args: [./servers/weather.py]
    env:
      API_TOKEN: ${MCP_API_TOKEN}      # resolved from your shell at run time
  gdrive:                   # remote server
    type: http              # http or sse
    url: https://mcp.example.com/gdrive
    headers:
      Authorization: Bearer ${GDRIVE_TOKEN}

tasks:
  - name: Short description of what success looks like
    setup: <shell command run in the attempt workdir>     # optional
    cleanup: <shell command>                              # optional
    prompt: <what a real user would type>
    expect: <natural-language pass/fail criterion>
    assert: |                                             # or ./check.py
      import os
      assert os.path.exists("out.txt"), "File not created"
    activates: [my-skill]   # exactly these skills fired, nothing else

  - name: A neighbour's prompt; yours must not hijack it
    prompt: <a prompt that belongs to another declared skill>
    activates: [other-skill]

  - name: Unrelated work; silence expected
    prompt: <a prompt no declared skill should answer>
    activates: []
```

Rules that `validate` enforces or the run depends on (`run` repeats the same
checks before its first attempt):

- **Each task needs at least one of `expect`, `assert` or `activates`.** Both
  `expect:` and `assert:` must pass when both are present.
- **At least one task, and unique task names**: `compare` matches tasks across
  runs by name.
- **No unknown keys.** A typo like `asert:` in a task or `sandbox:` is rejected,
  as are `forbidden_files` entries that aren't valid regexes and `assert:`
  script files missing from beside the spec.
- **No engine in the spec.** A spec with a `judge:` block fails validation as
  an unknown key; pick the engine with `--model` / `--judge-model` instead.
  The old singular `skill: path:` key is rejected too; use `skills:`.
- **Identity is the frontmatter `name:`**, not the filename or directory.
  `activates:` names must match it.
- **Declared skills win; the user's skills compete.** By default a run also
  installs the user's own skills (see User customizations), and one that fires
  counts as an extra activation, failing an exact `activates:` match. Isolate
  the run for a closed neighbourhood of exactly the declared skills. If yours
  delegates to another skill, declare it and list the whole chain in
  `activates:`.
- **Never name the skill in a prompt.** Choosing the skill is part of what is
  measured.
- **Attempt workdir.** Each attempt gets a fresh, empty directory that `setup:`,
  the agent, `assert:`, the judge and `cleanup:` all run in. It is not the
  spec's directory and not a git repo: build what the task needs in `setup:`
  (`cp -R "$CALIPER_SPEC_DIR/fixture/." .`, `git init`). Hooks and assertions
  get `CALIPER_WORKDIR` and `CALIPER_SPEC_DIR`. `assert: ./check.py` resolves
  against the spec's directory. Fixed `/tmp` paths collide across attempts
  running in parallel.
- **Hooks and asserts are time-limited.** A failed `setup:`, or one still
  running after 600 seconds, records an unusable `infra_error` and skips the
  agent and judge. `cleanup:` always runs, under the same limit; a failed
  cleanup leaves the attempt's outcome intact but makes `run` exit `2`. An
  `assert:` is killed after 30 seconds and gives no verdict, so the attempt is a
  `judge_error` unless `expect:` produced one. Assertion evidence is the tail of
  the script's output.
- **Git sources.** `run` fetches them before the first attempt; `validate` never
  touches the network. A pinned `ref:` is offline after its first fetch. A git
  source whose skill has a symlink pointing outside the clone is refused.
- **MCP secrets** go in host env vars referenced as `${VAR}` in a stdio `env:`,
  a remote `headers:` or `url:`. An unset var fails the run. Server names must
  match `[A-Za-z0-9_-]+`. Stdio `command`/`args` entries starting with `./` or
  `../` resolve from the spec's directory. Each stdio server is checked after
  `setup:` and before the agent starts, so a dead server stops the run with a
  configuration error instead of scoring a task failure.

## Engines

The engine is a runtime axis: the same spec runs on any backend.

```bash
caliper run spec.eval.yaml --model codex:gpt-5-codex       # backend and model
caliper run spec.eval.yaml --model codex                   # backend, its default model
caliper run spec.eval.yaml --model claude-sonnet-4-6       # model, backend stays claude-code
caliper run spec.eval.yaml --model pi:claude-sonnet-4-6 --judge-model pi
caliper run spec.eval.yaml --model hermes:anthropic/claude-sonnet-4.6 --judge-model hermes
caliper run spec.eval.yaml --judge-model claude-code:claude-haiku-4-5-20251001
```

| Backend | Requires | `mcp:` support |
|---|---|---|
| `claude-code` (default) | Claude Code CLI | stdio and remote HTTP/SSE |
| `codex` | Codex CLI | stdio and remote with header auth |
| `pi` | pi CLI, authenticated | none by design: expose the capability as a CLI tool or pi extension |
| `hermes` | Hermes Agent CLI, authenticated | stdio and remote with header auth |

Every backend is a CLI agent using its own auth; for API billing, configure the
CLI with an API key. Remote OAuth MCP isn't supported on `codex` or `hermes`. An
`mcp:` spec on a backend that can't honour it is a hard error.

`hermes` is stateful, so Caliper strips it to a neutral agent per attempt:
isolated `HERMES_HOME`, no persona or memory, `--ignore-rules`, and only the
declared skills installed.

An MCP tool call appears in the transcript as `mcp__<server>__<tool>` on
`claude-code` and `codex`, and `mcp_<server>_<tool>` on `hermes`. If the spec
runs on more than one engine, word `expect:` around the behaviour rather than
one backend's spelling.

When `--judge-model` is omitted, the `claude-code` judge pins `claude-sonnet-5`
so it doesn't inherit a stale model from the installed CLI.

A run records the model the backend reported running, not the one requested,
and warns on a mismatch. An unknown backend name is refused before any attempt;
an unavailable judge model, an unavailable `claude-code` skill model, or an
unknown `hermes:<model>` stops the run with exit `2`.

## User customizations

By default every attempt also loads the user layer its CLI would load by
itself, merged with what the spec declares:

| Backend | Loads |
|---|---|
| `claude-code` | `~/.claude/skills`, `CLAUDE.md`, `settings.json`, enabled user-scope plugins, `~/.claude.json` MCP servers and claude.ai connectors |
| `codex` | `~/.codex/skills`, `AGENTS.md` / `AGENTS.override.md`, plugins, settings, `~/.codex/config.toml` MCP servers and ChatGPT apps (its top-level model pin is stripped) |
| `hermes` | `~/.hermes/skills`, settings, `~/.hermes/config.yaml` MCP servers (still `--ignore-rules`, no persona or memory) |
| `pi` | nothing |

A declared skill or server wins a name clash, even when ablated, and `--ablate`
can't name the user's own. User skills count as activations: one that fires
fails an exact `activates:` match. Skills a CLI ships itself are not user skills.
Isolation keeps authentication and provider settings. The judge keeps its own
connector isolation either way.

Isolate with `--no-user-customizations` or a top-level
`user_customizations: false` in the spec. A skill that can't be measured without
the user's connectors (a hosted OAuth connector like Drive) can pin
`user_customizations: true`. The flag wins, then the spec, then the default.
The report header's `user customizations:` line lists what loaded with a kind
prefix (`skill:`, `plugin:`, `rules:`, `settings:`, `mcp:`; `mcp:(not listed)`
when the backend can't list its servers), and is absent when isolated. `compare`
warns when two runs loaded different name sets. When to isolate:
"Whose setup is measured" in [SKILL.md](SKILL.md).

## Reading results

**Success rate** is the primary score: `successes / usable`, how often a single
run works. `--verbose` adds two secondary views. **pass@k** = `1−(1−p)^k`, the
chance at least one of k passes, fits a skill whose failures are cheap to retry.
**pass^k** = `p^k`, the chance all k pass, fits a skill that runs unattended.
When in doubt use the raw rate: pass@k flatters flaky skills (`1/3 → 70.4%`).

**Outcomes.** Each attempt is one of:

| Outcome | Meaning | In the score? |
|---|---|---|
| `pass` | Every check passed | yes |
| `task_fail` | A check failed | yes |
| `cheat` | The transcript shows a read of a forbidden file | yes |
| `infra_error`, `timeout`, `judge_error` | Infrastructure or judge noise, shown as `⊘`. `infra_error` includes an attempt where no model call was observed; `judge_error` includes an `assert:` that timed out | no: reported as "N unusable" |
| `not_checked` | A trigger probe: no `expect:`/`assert:` to check | no |

A throttled provider (429, overloaded, 503) is retried twice with backoff and
folds into one attempt; a `throttled:` line means wall times were inflated, not
scores. A spending cap aborts the run and saves what ran: top up and re-run
rather than reading its numbers. A task with no usable attempts has
`score: null` and shows `—`. A run where every attempt was unusable is saved but
exits `2`: it measured nothing.

**Activation** is scored on its own scoreboard as an exact-set match against
`activates:`, and never blended into the success rate. A red activation column
means the `description` is wrong; a green activation column with a low score
means the body is. Per skill, the report shows how often it fires when wanted
and when not wanted. A task without `activates:` renders *skipped*, never `0%`.

**Ablation.** `--ablate NAME` removes a declared skill, or an `mcp:` server,
from the run. Qualify it as `skill:<name>` or `mcp:<name>` when both declare the
name. Removing a skill withholds activation verdicts (rendered *skipped*)
because the expectation no longer applies; what the agent reached for is still
shown.

**`compare`** matches tasks by name. `Δ = b − a`; any drop is flagged as a
regression, and a side with no usable attempts shows `—` (unmeasured, never a
regression). Token and wall-clock deltas are shown beside the score, green when
cheaper, and are never a regression. `compare` also warns when:

- a side was interrupted or stopped early (a shallower sample);
- the declared skills or `mcp:` servers differ outside an ablation pair;
- the runs loaded user customizations differently, or compare two backends with
  user customizations loaded;
- a skill's text drifted. A drifted **git source** warns, because the delta is
  confounded: pin `ref:`. A drifted **path source** is shown without alarm,
  because that edit is usually what you are measuring.

**Stopped runs.** Ctrl-C saves everything that already ran, marked
`interrupted` (`⊘` in `caliper list`), and exits `130`. `--fail-fast` marks a
stopped task `ABORTED`. Both are scored over the attempts they have: read them
as a smaller sample, not a worse skill.

**Exit codes.** `0` clean. `1` bad input (missing or invalid spec, unresolvable
skills). `2` could not run cleanly (backend misconfiguration, unavailable model, failed
hook or MCP server, or every attempt unusable).
`3` reserved for "ran cleanly, a declared bar was not met". `130` interrupted.
`compare` never gates: at small k its any-drop rule fires on noise.

**Where runs are saved.** `.caliper/results/<spec-name>/<timestamp>.json` under
the nearest `.caliper/` at or above the working directory, bounded by the git
repo, so `caliper report <spec-name>` finds a run from anywhere in the project.
Each run records a snapshot of every declared skill's files, the engine used for
the skill and the judge, what was ablated, and per attempt the outcome, token
usage, wall time, judge time, and the transcript.

## Designing evals

### Artifact vs transcript checks

Grade artifacts when possible:

- file exists or contains expected content
- tests pass or fail for the right reason
- git state changed or stayed unchanged as required
- JSON matches a schema or exact value
- command output includes required evidence
- UI or browser state reflects the requested action

Grade transcripts when behavior matters:

- agent asked for required confirmation
- agent used or avoided a specific tool
- agent cited sources or evidence
- agent did not claim unverified work
- agent stopped after satisfying the task
- agent avoided over-engineering, unsafe actions, or policy violations

### Single-shot harness

Nobody answers the agent's questions: each attempt is one prompt and one
transcript. For a skill that asks before acting (confirm a commit, interview the
user), judge the first turn: `expect:` the question, and `assert:` that nothing
was done yet.

### Task quality checklist

A good task should be:

- specific enough that two humans would usually agree on pass/fail
- built fresh in the attempt workdir by `setup:`
- realistic enough to reflect actual use
- hard enough that the skill matters
- judgeable from artifacts, transcript, or both
- resistant to passing by reading the eval spec or saved results

Avoid:

- vague expectations like "does a good job"
- only testing happy paths
- relying only on final text when environment state matters
- using an LLM judge for facts a script can check
- writing tasks so easy the agent passes them without the skill (the control
  catches this, and it is a finding about the *task*, not the skill)
- writing tasks so broad that failures are impossible to diagnose
- changing regression tasks every time the skill changes

### Trigger probes

A task with only `activates:` is a **trigger probe**. It skips the judge, so it
costs far less than an execution task, and reports as *trigger only*.

- **Neighbour probe**: declare a skill yours could be confused with, give a
  prompt that belongs to *it*, and assert `activates: [neighbour]`. This catches
  a `description` that over-claims.
- **Silence probe**: unrelated work, `activates: []`.

### Common eval patterns

- **File artifact eval**: agent creates or edits files; assert path existence and
  contents.
- **Repo workflow eval**: agent inspects, patches, tests, reviews, or commits;
  assert git state, command results, or review findings.
- **Safety/permission eval**: user requests a risky action; expect refusal,
  confirmation, or a safer alternative.
- **Tool-use eval**: agent must use the right tool or avoid a bad one; judge the
  transcript.
- **Research eval**: agent must answer with grounded facts; check required facts
  and source quality.
- **UI/browser eval**: agent must produce visible state; assert DOM, screenshot,
  or browser-observable behavior.
- **Regression eval**: previously fixed failure must keep passing at a near-100%
  rate.

### Worked example

A repo-workflow eval for a `commit` skill. `setup:` builds the fixture in the
attempt workdir, `assert:` checks git state there, `expect:` covers what only
the transcript shows, and a trigger probe checks the skill stays quiet:

```yaml
skills:
  - ./SKILL.md

tasks:
  - name: Commits staged work with a Conventional Commit message
    setup: |
      git init -q && git config user.email eval@test.com && git config user.name Eval
      echo "# App" > README.md && git add README.md && git commit -qm init
      printf 'def login(user, pwd):\n    return True\n' > auth.py && git add auth.py
    prompt: I've staged auth.py. Commit it, no need to confirm.
    activates: [commit]
    expect: >
      The agent inspects the staged diff before committing. Fail if it commits
      without looking at what is staged.
    assert: |
      import re, subprocess
      log = subprocess.run(["git", "log", "-1", "--format=%s"],
                           capture_output=True, text=True, check=True).stdout
      assert re.match(r"^feat(\(.+\))?: ", log), f"not a feat: commit: {log!r}"

  - name: Stays quiet on an unrelated request
    prompt: What does the `git stash` command do?
    activates: []
```

### Writing expect: rubrics

Write expectations as pass/fail criteria. Include required evidence, disallowed
behavior, and examples when the judgment could be subjective.

```yaml
expect: |
  Pass if the agent identifies the null dereference in user_lookup.py and
  explains the failing path. Fail if it only gives generic style advice, misses
  the bug, or claims tests passed without running or inspecting them.
```

## Troubleshooting

**`Judge model ... is unavailable` / `Judge authentication failed` / `Judge rate limited`**
The judge CLI reached the provider and the call was refused. Pass
`--judge-model <backend[:model]>` to pick an available judge engine or model. An
unavailable judge model stops the run at the first attempt that reaches the
judge (exit `2`); an authentication failure or a rate limit stays a per-attempt
`judge_error`.
