# Caliper Reference

## Commands

### Run an evaluation
```bash
caliper run path/to/spec.eval.yaml --k 3
caliper run path/to/spec.eval.yaml --k 3 --baseline      # include no-skill delta
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
caliper list my-skill-eval          # all runs for one spec
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
skill:
  path: ./SKILL.md

sandbox:
  forbidden_files:
    - ".*\\.eval\\.yaml$"   # agent cannot read the spec

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
```

The same spec runs on any engine. To run it on Codex, pass the backend at run
time — the spec is unchanged:

```bash
caliper run path/to/spec.eval.yaml --model codex --judge-model codex
```

For pi (loads the skill natively via pi's `--skill` flag), the `:model` half
overrides pi's configured default:

```bash
caliper run path/to/spec.eval.yaml --model pi:claude-sonnet-4-6 --judge-model pi
```

For hermes (Nous Research), the `:model` half is a `provider/model` value passed
straight to hermes's `-m`; omit it to use your `~/.hermes/config.yaml` default:

```bash
caliper run path/to/spec.eval.yaml --model hermes:anthropic/claude-sonnet-4.6 --judge-model hermes
```

Hermes is a stateful agent, so Caliper normalizes it to a neutral agent per
attempt (isolated `HERMES_HOME`, no persona/memory, `--ignore-rules`, the
skill-under-test staged as the only local skill) and recovers the full tool-call
trajectory by running `hermes -z` then `hermes sessions export`.

## Key concepts

- **success rate** — the primary score: `successes / usable` (how often a *single* run works), computed over the usable attempts only. **pass@k** (P(≥1 of k pass), retry-optimistic) and **pass^k** (P(all k pass), strict) are secondary views, on every task in the JSON (`pass_at_k`/`pass_hat_k`) and under `--verbose`
- **outcome** — each attempt is typed `pass`, `task_fail`, `cheat`, `infra_error`, `timeout`, or `judge_error`; the last three are *unusable* (infrastructure/judge noise) and are excluded from the score denominator and reported as a separate "N unusable" count, so a throttled or judge-flaked run is not mistaken for a regression. `passed` in the JSON equals `outcome == pass`.
- **`--fail-fast N`** — optional run control that stops scheduling new attempts for a task after N consecutive `infra_error`/`timeout` outcomes; `0` disables it. Early-stopped tasks report as `ABORTED`, and tasks with no usable attempts keep `score: null`.
- **baseline** — runs each task without the skill to compute a delta score
- **judge** — the spec drives evaluation: `expect:` triggers an LLM verdict (which may generate a Python assertion script); `assert:` runs a deterministic Python script; both can be combined and both must pass
- **cheat detection** — transcript is scanned for reads of forbidden files (spec, results)
- **token & wall-clock usage** — each attempt records an optional `usage` (`input_tokens` non-cached, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, computed `total_tokens`; the four token fields are disjoint) plus its `duration_seconds`. `report` shows per-task `Tokens`/`Wall` columns in the results table plus a per-run `Tokens … in / … out · Wall …` line (unusable spend broken out separately); a `--baseline` run retains the full no-skill run (`RunResults.baseline_task_results`) and renders through the same `compare` view (side-by-side table + token/wall deltas); `compare` deltas (green = cheaper) are **never** a regression — only the score is. All usage fields are optional (`null` → renders `—`); `claude-code`, `codex`, `pi`, `hermes` all report tokens. **Dollar cost is deliberately not tracked** (inconsistent across backends; tokens are the volume signal).
- **isolation** — each attempt runs in a fresh temp HOME with no session history
- **engine as a runtime axis** — backend + model are not spec fields; they are chosen per run and recorded in `RunMeta` (skill `backend`/`model` **and** `judge_backend`/`judge_model`), so the same spec can target any agent and never ages when a model goes stale. A default-model run records the concrete model the agent resolved wherever the backend reports it (skill model from hermes' export, `judge_model` from the claude-code judge's JSON), not a bare "default"; `judge_model` is empty for an assert-only run where no LLM judge fired
- **`--model TARGET`** — select the skill engine at run time (default `claude-code`); accepts `backend:model`, bare backend (`codex`), or bare model name
- **`--judge-model TARGET`** — same syntax, selects the judge engine independently

## Results storage

Results are saved automatically to `.caliper/results/<spec-name>/<timestamp>.json`
alongside the spec file. Each result includes a full skill snapshot (content + git SHA
of the skill file and any referenced scripts) for reproducibility. Each attempt
records its `outcome` (see above), an optional `usage` block (token counts), and
per-task results include an `unusable` count; a task with no usable attempts has
`score: null`. When `--fail-fast N` stops a task early, that task may contain
fewer than k attempt records. Run-level usage totals are **derived** at render
time, not persisted — the saved JSON holds only per-attempt `usage`, while
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
- writing tasks so easy the baseline passes consistently
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
