# Caliper Reference

## Commands

### Run an evaluation
```bash
caliper run path/to/spec.eval.yaml --k 3
caliper run path/to/spec.eval.yaml --k 3 --ablate my-skill   # same tasks, that skill removed
caliper run path/to/spec.eval.yaml --k 3 --ablate a --ablate b  # repeatable; name them all for the bare agent
caliper run path/to/spec.eval.yaml --verbose             # show per-attempt reasoning

# Choose the engine at run time — it is not stored in the spec (default: claude-code)
caliper run path/to/spec.eval.yaml --model codex:gpt-5-codex
caliper run path/to/spec.eval.yaml --model codex                # backend only, its default model
caliper run path/to/spec.eval.yaml --model claude-sonnet-4-6    # model only, backend stays claude-code
caliper run path/to/spec.eval.yaml --judge-model claude-code:claude-haiku-4-5-20251001
caliper run path/to/spec.eval.yaml --model codex --judge-model claude-code:claude-haiku-4-5-20251001
```

### Validate a spec file
```bash
caliper validate path/to/spec.eval.yaml
```

### Browse saved results
```bash
caliper list                        # all specs with latest scores
caliper list my-skill-eval          # all runs for one spec: Run id + which were ablated
caliper report my-skill-eval        # latest run (table view)
caliper report my-skill-eval --run 2026-05-12T14-23-01Z  # specific run
caliper report results.json --format json
```

### Compare two runs (ablation)
Diff two already-saved runs of the same eval — full vs. shortened skill, or the
same skill over time. Tasks are matched by name; `Δ = b − a`; a negative Δ flags
a regression; a side with no usable attempts shows `—` (unmeasured, never a
regression); the headline `Δ (matched)` averages only tasks measured on both
sides. Each argument is addressed like `report` (spec name → latest run, or a
results-JSON path); pin a historical run by naming its path.
```bash
caliper compare full-eval short-eval          # latest run of each spec
caliper compare a.json b.json                 # pin specific runs
caliper compare full-eval short-eval --format json   # for a ship/no-ship gate
```

## Spec format (.eval.yaml)

The spec carries no engine — no `backend`/`model` and no `judge:` block. Backend
and model for both the skill and the judge are chosen at run time via `--model` /
`--judge-model` (default `claude-code`); a spec that still pins these keys fails
validation with a message pointing at the flags.

```yaml
skills:                   # installed at the agent's own skills root, never
  - ./SKILL.md            #   preloaded — the agent has to choose it
  # add further entries to test that yours is the one that fires (they are
  # assertable via `activates:`, not decoration). A bare string is a *path
  # source*; a mapping is a *git source* caliper clones for you:
  - repo: vercel-labs/agent-skills
    ref: a1b2c3d          # optional — omit to track the default branch
    path: skills/tdd/SKILL.md   # optional — defaults to SKILL.md at the root

sandbox:
  forbidden_files:
    - ".*\\.eval\\.yaml$"   # agent cannot read the spec

mcp:                        # optional — MCP servers the agent may use
  weather:                  # server name → mcp__weather__<tool> in the transcript
    command: python3        # local stdio command the harness spawns
    args: [./servers/weather.py]
    env:
      API_TOKEN: ${MCP_API_TOKEN}   # resolved from your shell at run time
  gdrive:                   # remote (hosted) server reached over HTTP/SSE
    type: http              # http or sse
    url: https://mcp.example.com/gdrive
    headers:
      Authorization: Bearer ${GDRIVE_TOKEN}   # resolved from your shell at run time

tasks:
  - id: task-001
    name: Short description of what success looks like
    setup: <shell command to prepare the environment>    # optional
    cleanup: <shell command to tear down>               # optional
    prompt: <prompt sent to the AI under evaluation>
    expect: <natural language description of a successful outcome>

  - id: task-002
    name: Task with a deterministic assertion
    prompt: Write hello to /tmp/out.txt
    expect: A file is created at /tmp/out.txt
    assert: |
      import os
      assert os.path.exists("/tmp/out.txt"), "File not created"

  - id: task-003
    name: Task with external assertion script
    prompt: Generate a report
    assert: ./assertions/check_report.py

  - id: task-004
    name: A neighbour's prompt — yours must not hijack it
    prompt: <a prompt that belongs to a different declared skill>
    activates: [other-skill]      # exactly these fired, and nothing else

  - id: task-005
    name: Unrelated work — silence expected
    prompt: <a prompt no declared skill should answer>
    activates: []                 # nothing fired
```

Each task needs at least one of `expect`, `assert` or `activates`.

`activates:` asserts the **exact set** of skills the agent loaded on an attempt.
Skills are installed where the agent looks for them and never pasted into the
prompt, so *choosing* one is an observable. A task carrying only `activates:` is
a **trigger probe**: it skips the judge entirely (much cheaper than an execution
task) and reports as `trigger only`, not a zero. Activation is scored on its own
scoreboard and never blended into the success rate — a failing `description` and
a failing body are fixed in different places.

Identity is the frontmatter `name:`, so `activates:` names must match the
`name:` in each declared `SKILL.md`, not its filename or directory. A skill the
spec doesn't declare is never installed and can never activate — so if yours
delegates to another skill, declare it and enumerate the chain.

The same spec runs on any engine. To run it on Codex, pass the backend at run
time — the spec is unchanged:

```bash
caliper run path/to/spec.eval.yaml --model codex --judge-model codex
```

For pi, the `:model` half overrides pi's configured default:

```bash
caliper run path/to/spec.eval.yaml --model pi:claude-sonnet-4-6 --judge-model pi
```

For hermes (Nous Research), the `:model` half is a `provider/model` value passed
straight to hermes's `-m`; omit it to use your `~/.hermes/config.yaml` default:

```bash
caliper run path/to/spec.eval.yaml --model hermes:anthropic/claude-sonnet-4.6 --judge-model hermes
```

Hermes is a stateful agent, so Caliper normalizes it to a neutral agent per
attempt (isolated `HERMES_HOME`, no persona/memory, `--ignore-rules`, and only
the spec's declared skills installed) and recovers the full tool-call
trajectory by running `hermes -z` then `hermes sessions export`.

## Key concepts

- **success rate** — the primary score: `successes / usable` (how often a *single* run works), computed over the usable attempts only. Two secondary views (on every task in the JSON as `pass_at_k`/`pass_hat_k`, and under `--verbose`) reframe it for how the skill is used: **pass@k** = `1−(1−p)^k` = P(≥1 of k pass) — the retry / "eventual success" lens, **≥** the rate, for when a failure is cheap to retry and you keep the good run; **pass^k** = `p^k` = P(all k pass) — the strict / "must never fail" lens, **≤** the rate, for when the skill runs unattended or as one link in a chain. When in doubt use the raw rate — pass@k is the code-gen metric and flatters flaky skills (`1/3 → 70.4%`)
- **outcome** — each attempt is typed `pass`, `task_fail`, `cheat`, `infra_error`, `timeout`, `judge_error`, or `not_checked`; `infra_error`/`timeout`/`judge_error` are *unusable* (infrastructure/judge noise) and are excluded from the score denominator and reported as a separate "N unusable" count, so a throttled or judge-flaked run is not mistaken for a regression. `not_checked` is neither: the task authored no `expect:`/`assert:` (a trigger probe), so it leaves the denominator without being reported as an error. `passed` in the JSON equals `outcome == pass`.
- **`--fail-fast N`** — optional run control that stops scheduling new attempts for a task after N consecutive `infra_error`/`timeout` outcomes; `0` disables it. Counts **attempts**, not invocations, so with retries it can cost up to three times the spawns. Early-stopped tasks report as `ABORTED`, and tasks with no usable attempts keep `score: null`.
- **`--workers N`** — how many **attempts** run in parallel, across all tasks (default `4`); every (task, attempt) pair is scheduled independently, so `--k 10 --workers 4` on a one-task spec runs four at a time. `--fail-fast` is the exception: it keeps each task's attempts sequential, since the streak it counts is only defined in order. Concurrent agents share one upstream rate limit, so a high `--workers` buys wall-clock time at the risk of more `infra_error` attempts.
- **retry** — an *attempt* is one measured shot at the task, an *invocation* one spawn of the agent; they differ when the provider refuses. A throttle (429 / overloaded / 503) is retried twice with backoff and folds into **one** `AttemptRecord` with a `retries` count — the score's denominator is attempts, never spawns. A **spending cap** is not retried and not survivable: it aborts the run (saving what ran, reporting the cap), because every remaining attempt would meet the same wall. A bare crash is neither retried nor aborted on — it reproduces. The merged record takes its *output* from the last invocation, **sums** tokens across all of them, and sums only the spawn time, excluding backoff (docs/adr/0019).
- **judge time** — `AttemptRecord.judge_seconds` is the wall-clock the judge spent grading one attempt, `null` when no judge ran (assert-only task, or an attempt that exited before reaching one). A **sibling** of `duration_seconds`, never folded into it — that field stays the harness spawn, so `Wall` means the same thing it always did. The run summary adds a `Judge` line averaged over *graded* attempts only.
- **empty runs are not saved** — a run that stopped before any attempt finished (a spending cap on the first invocation, a Ctrl-C during skill fetching) writes no results file. Salvage keeps what you paid for; an empty run has `avg_score` 0.0 over zero scored tasks, which would render as `0.0%` in `caliper list`.
- **exit codes** — `0` clean, `1` bad input (missing/invalid spec, unresolvable skills), `2` could not run (backend misconfiguration), `3` reserved for "ran cleanly, a declared bar was not met", `130` interrupted with Ctrl-C (partial run saved). `2` vs `3` is the distinction CI needs: a broken pipeline is not the same as a skill that missed the bar. `compare` never gates — its regression flag is the any-below rule, which at small k fires on noise as often as on a real change, so gating belongs on a pre-registered bar rather than on a diff.
- **interrupted run** — Ctrl-C stops a run *cooperatively*: the agents in flight are killed, unstarted attempts are skipped, and everything that already ran is saved as an ordinary run file with `interrupted: true` in `RunMeta` (exit code `130`; a second Ctrl-C quits without saving). Attempts the interrupt killed are dropped rather than recorded as `infra_error`. Scored over the attempts it has, exactly like a `--fail-fast` truncation — the marker is what distinguishes "stopped by a human" from "stopped on purpose". A fatal backend error mid-run saves the same way before reporting (docs/adr/0018).
- **ablation (`--ablate NAME`)** — runs the same tasks with that declared skill *removed* from the neighbourhood, saved as an ordinary run; `caliper compare` gives the delta. Repeatable; naming every declared skill leaves the bare agent. The ablated arm is a property of the **tasks and the surviving neighbourhood**, never of the removed skill's text — that skill is not installed, so neither its body nor its `description` can move the number — so run it **once** and re-diff against it as the skill changes. It observes activation but withholds the verdict (`activates:` expectations are dropped, rendered *skipped*, never `0%`), because filtering the expectation would assert a claim the author never wrote. `RunMeta.ablated` records what was removed, which is how `compare` labels an ablation pair instead of warning about the neighbourhood difference. Replaces the retired `--baseline` flag (docs/adr/0015)
- **skill source** — how one `skills:` entry is obtained. A bare string is a **path source** (a `SKILL.md` on disk, resolved against the spec's directory); a mapping is a **git source** (`repo:`, optional `ref:`, optional `path:` defaulting to a root `SKILL.md`) that caliper clones into a commit-addressed cache. Git sources are how a spec gives a `description` real competition without vendoring someone's repo. One entry is one skill; entries sharing a repo and commit share one clone. `ref:` is optional and an omitted one tracks the default branch — allowed rather than forbidden because the resolved commit is recorded and `compare` reports drift, though a pinned commit is fully offline after its first fetch while an unpinned one costs a `git ls-remote` per run. `run` fetches before the first attempt; `validate` never touches the network (it resolves from a warm cache and reports the rest as *not cached*). An uncached, unfetchable source **refuses the run**; a cached one whose remote is unreachable runs on the cache and warns (docs/adr/0016, docs/adr/0017)
- **skill drift** — a member of the neighbourhood whose *text* differs between two saved runs, reported by `compare` from the per-file hashes in `SkillSnapshot`. Graded by provenance, not role: a drifted **git source** warns (the spec claimed where those bytes came from, so the delta is confounded), a drifted **path source** is shown without alarm (nothing was promised about a working file, and that edit is usually what the run exists to measure). Distinct from the neighbourhood warning, which is a change in *membership* rather than text
- **judge** — the spec drives evaluation: `expect:` triggers an LLM verdict (which may generate a Python assertion script); `assert:` runs a deterministic Python script; both can be combined and both must pass
- **cheat detection** — transcript is scanned for reads of forbidden files (spec, results)
- **MCP servers (`mcp:`)** — an optional top-level mapping (keyed by server name) declaring MCP servers the agent-under-test may use; they are a capability granted to the agent for the eval — part of the run environment like `sandbox:` (a sibling of it and of `skills:`, applied whether or not any skill is declared), so they live in the spec, not behind a flag. A server is either **local stdio** (`command`, `args`, `env`) or **remote** (`type: http`/`sse`, `url`, optional `headers`); the two field sets are mutually exclusive. Supported on **`claude-code`** (stdio + remote HTTP/SSE), **`hermes`** (stdio + remote header-auth; hermes translates the block into its native `mcp_servers` config in the isolated `HERMES_HOME`, overwriting your personal servers, and cannot do remote OAuth — that needs an interactive browser flow), and **`codex`** (stdio + remote header-auth; codex translates the block into `[mcp_servers.*]` tables in the isolated `~/.codex/config.toml` — stdio as `command`/`args`/`env`, remote as `url` + a static `http_headers` map, with `http`/`sse` collapsed onto codex's one url-inferred streamable-HTTP transport — replacing any personal servers from your real config, and likewise cannot do remote OAuth); a spec that declares `mcp:` on a backend that can't honor it is a hard error, not a silent no-op (`pi` has no MCP by design and will not honor `mcp:` natively — expose the capability as a CLI tool the skill drives or a pi extension, or run the eval on `claude-code`/`hermes`/`codex`). A value in a stdio `env:`, a remote `headers:`, or a remote `url:` may reference a host env var as `${VAR}` (resolved at the harness boundary from your shell at run time so secrets stay out of the committed spec; an unset var fails the run). A tool call surfaces as a namespaced name — `mcp__<server>__<tool>` on `claude-code` and `codex`, `mcp_<server>_<tool>` on `hermes` — so an `expect:` judge can check a tool was used; word it around behaviour, not one backend's spelling, if the spec runs under more than one engine. Server names must match `[A-Za-z0-9_-]+`; `caliper validate` reports a malformed entry (bad name, unknown key/`type`, a stdio server missing `command`, or a remote server missing `url`)
- **token & wall-clock usage** — each attempt records an optional `usage` (`input_tokens` non-cached, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, computed `total_tokens`; the four token fields are disjoint) plus its `duration_seconds`. `report` shows per-task `Tokens`/`Wall` columns in the results table plus a per-run `Tokens … in / … out · Wall …` line (unusable spend broken out separately); an ablated run is an ordinary saved run, so the skill-vs-bare-agent view is `caliper compare` like any other diff (side-by-side table + token/wall deltas); `compare` deltas (green = cheaper) are **never** a regression — only the score is. All usage fields are optional (`null` → renders `—`); `claude-code`, `codex`, `pi`, `hermes` all report tokens. **Dollar cost is deliberately not tracked** (inconsistent across backends; tokens are the volume signal).
- **isolation** — each attempt runs in a fresh temp HOME with no session history
- **engine as a runtime axis** — backend + model are not spec fields; they are chosen per run and recorded in `RunMeta` (skill `backend`/`model` **and** `judge_backend`/`judge_model`), so the same spec can target any agent and never ages when a model goes stale. A default-model run records the concrete model the agent resolved wherever the backend reports it (skill model from hermes' export, `judge_model` from the claude-code judge's JSON), not a bare "default"; `judge_model` is empty for an assert-only run where no LLM judge fired. When `--judge-model` is omitted, the claude-code judge still pins `claude-sonnet-5` at execution time so it does not inherit a stale model from the installed Claude CLI — that pin is not written into `RunMeta` unless you pass it explicitly or the autorater reports what it used
- **`--model TARGET`** — select the skill engine at run time (default `claude-code`); accepts `backend:model`, bare backend (`codex`), or bare model name
- **`--judge-model TARGET`** — same syntax, selects the judge engine independently; omit it to use the default `claude-code` backend, which pins `claude-sonnet-5` at execution time (not recorded in `RunMeta` unless the autorater reports it)

## Results storage

Results are saved automatically to `.caliper/results/<spec-name>/<timestamp>.json`
alongside the spec file. Each result includes a full skill snapshot (content + git SHA
of the skill file and any referenced scripts) for reproducibility. Each attempt
records its `outcome` (see above), an optional `usage` block (token counts), an
optional `transcript` array (ordered turns with `tool_name`/`tool_input`/`tool_output`
when present), and per-task results include an `unusable` count; a task with no usable
attempts has `score: null`. When `--fail-fast N` stops a task early, or a run was
interrupted, that task may contain fewer than k attempt records; an interrupted
run also carries `interrupted: true` in `RunMeta`, is flagged with `⊘` in
`caliper list`, and makes `caliper compare` warn that one side is a shallower
sample. Each attempt may also carry `judge_seconds` (`null` when no judge ran) and
`retries` (0 unless the provider was throttling). Run-level usage totals are **derived** at
render time, not persisted — the saved JSON holds only per-attempt `usage`, while
`report --format json` adds a computed `usage_totals` block.

## Designing good evals — full guidance

(Referenced from SKILL.md → "Designing good evals". Read and apply these when designing eval tasks.)

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

### Task quality checklist

A good task should be:

- specific enough that two humans would usually agree on pass/fail
- isolated from previous attempts by `setup:` and `cleanup:`
- realistic enough to reflect actual use
- hard enough that the skill matters
- judgeable from artifacts, transcript, or both
- resistant to passing by reading the eval spec or saved results

Avoid:

- vague expectations like "does a good job"
- only testing happy paths
- relying only on final text when environment state matters
- using an LLM judge for facts a script can check
- writing tasks so easy the bare agent passes consistently (an ablated run is how you catch this — and it is a finding about the *task*, not the skill)
- writing tasks so broad that failures are impossible to diagnose
- changing regression tasks every time the skill changes

### Common eval patterns

- **File artifact eval** — agent creates or edits files; assert path existence and
  contents.
- **Repo workflow eval** — agent inspects, patches, tests, reviews, or commits;
  assert git state, command results, or review findings.
- **Safety/permission eval** — user requests a risky action; expect refusal,
  confirmation, or a safer alternative.
- **Tool-use eval** — agent must use the right tool or avoid a bad one; judge the
  transcript.
- **Research eval** — agent must answer with grounded facts; check required facts
  and source quality.
- **UI/browser eval** — agent must produce visible state; assert DOM, screenshot,
  or browser-observable behavior.
- **Regression eval** — previously fixed failure must keep passing at a near-100%
  rate.

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
The judge CLI reached the provider and the call was refused. Caliper classifies these at the harness boundary (from the CLI's structured output) and suggests passing `--judge-model <backend[:model]>` to pick an available judge engine or model.
