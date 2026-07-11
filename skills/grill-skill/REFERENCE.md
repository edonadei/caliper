# Grill Skill Reference

## Caliper commands used by this skill

```bash
# Check spec is valid before running
caliper validate path/to/spec.eval.yaml

# First run — fast, catches spec errors
caliper run path/to/spec.eval.yaml --k 1

# Reliability run — after iterating on the skill
caliper run path/to/spec.eval.yaml --k 3

# Baseline run — before committing, proves the skill makes a difference
caliper run path/to/spec.eval.yaml --k 3 --baseline

# Choose the engine at run time — it is not stored in the spec (default: claude-code)
caliper run path/to/spec.eval.yaml --model codex:gpt-5-codex
caliper run path/to/spec.eval.yaml --model codex
caliper run path/to/spec.eval.yaml --judge-model claude-code:claude-haiku-4-5-20251001

# Browse past results
caliper list
caliper report path/to/spec.eval.yaml

# Compare two saved runs of the same eval (ablation: full vs. shortened, or over time)
caliper compare full-eval short-eval           # spec name -> latest run, or a results-JSON path
caliper compare a.json b.json --format json     # per-task Δ, regression flags, for scripting
```

`caliper compare <A> <B>` diffs two already-saved runs task by task: tasks are
matched by name, `Δ = b − a`, a negative Δ flags a regression (any-below), and a
side with no usable attempts shows `—` (unmeasured, never a regression) so
infra/judge noise can't fake a loss. Under the success-rate headline, `compare` also
shows **token and wall-clock deltas** (green = cheaper) — the "same quality, 40%
fewer tokens" signal an ablation looks for. These are secondary: a token/time
change is **never** a regression (only the score is), and dollar cost is not tracked
(tokens are the volume signal). Each attempt in the report also shows its tokens
next to its duration under `--verbose`.

## Inspecting failures

After any `caliper run`, failed tasks are shown automatically with their output
and `assert_evidence` — no extra command needed. Each attempt is tagged with an
`outcome`: a real `task_fail` reads as `✗`, while *unusable* attempts
(`infra_error` from a rate-limit / spending-cap, `timeout`, or `judge_error`)
read as `⊘` and are excluded from the score denominator, with a separate
"N unusable" count in the summary — so a throttled or judge-flaked run is not
mistaken for a skill regression. If `caliper run --fail-fast N` stopped a task
after repeated `infra_error` / `timeout` outcomes, the report marks it as
`ABORTED` and shows how many attempts ran. If a failure is still unclear, use
`--verbose` to see full output for all tasks (including passing ones):

```bash
# Full output for all tasks (passing + failing), untruncated
caliper report path/to/spec.eval.yaml --verbose

# Or inspect a specific past run
caliper report path/to/spec.eval.yaml --run 2026-06-21T14-53-12Z --verbose
```

## Spec skeleton

The spec carries no engine — pick the backend/model at run time with `--model` /
`--judge-model` (default `claude-code`).

```yaml
skill:
  path: ./SKILL.md

sandbox:
  forbidden_files:
    - ".*\\.eval\\.yaml$"
    - "./.caliper/.*"

# Optional — only if the skill needs MCP tools. stdio + claude-code only for now.
mcp:
  weather:                       # → mcp__weather__<tool> in the transcript
    command: python3
    args: [./servers/weather.py]
    env:
      API_TOKEN: ${MCP_API_TOKEN}   # resolved from your shell at run time

tasks:
  - name: Happy path — <what success looks like>
    setup: <optional shell command>
    cleanup: <optional shell command>
    prompt: <prompt sent to the agent>
    expect: <natural-language success criterion>
    assert: |
      # optional deterministic check

  - name: Edge case — <tricky but valid input>
    prompt: ...
    expect: ...

  - name: Adversarial — <what the skill should refuse or avoid>
    prompt: ...
    expect: <describes the refusal or safe behavior>
```

## Naming convention

The spec file lives next to the skill and shares its directory name:

```
skills/my-skill/SKILL.md
skills/my-skill/my-skill.eval.yaml   ← generated here
```

## Writing good expect: criteria

Be specific about evidence. Include what the judge should look for and what counts as failure.

```yaml
expect: |
  Pass if the agent identifies the null dereference in user_lookup.py and
  explains the failing path. Fail if it only gives generic style advice,
  misses the bug, or claims tests passed without running them.
```

## When to use assert:

Add `assert:` when the outcome is a fact that an LLM judge might guess wrong:
- File exists or contains exact content
- Command exit code or output
- Git state (staged, committed, clean)
- JSON schema or exact value
- Test suite passes or fails

## MCP servers (`mcp:`)

If the skill under test needs MCP tools, declare them in a top-level `mcp:` block (a mapping keyed by server name) — a capability granted to the agent-under-test for the eval, part of the run environment like `sandbox:` (a sibling of it, not nested under `skill:`), so they belong in the spec, not on the command line. This release supports **local stdio servers on the `claude-code` backend only**: each entry has a `command`, optional `args`, and optional `env`. A tool call appears in the transcript as `mcp__<server>__<tool>`, so an `expect:` criterion can check the skill actually used it. Put secrets in a host env var and reference it as `${VAR}` inside `env:` — it resolves from your shell at run time and never lands in the committed spec (an unset var fails the run). Running an `mcp:` spec on a non-`claude-code` backend is a hard error, not a silent no-op.

## Backends

| Backend | Requires | Notes |
|---|---|---|
| `claude-code` | Claude Code CLI | Default for most skills |
| `codex` | Codex CLI | For Codex-targeted skills |
| `pi` | pi CLI (authenticated) | For pi / agentskills.io skills; native `--skill` loading |
| `hermes` | Hermes Agent CLI (authenticated) | Nous Research; normalized to a neutral agent, `hermes:<provider>/<model>` picks the model |

The skill engine (`--model`) and judge engine (`--judge-model`) are chosen independently at run time. Every backend is a CLI agent; for API billing, configure a CLI with an API key rather than selecting a separate backend.

`hermes` is a stateful agent (persistent memory + persona), so Caliper strips it to a neutral agent per attempt — isolated `HERMES_HOME`, no `SOUL.md`/`MEMORY.md`, `--ignore-rules`, skill-under-test staged as the only local skill — and recovers the full trajectory via `hermes sessions export` after the `hermes -z` run.
