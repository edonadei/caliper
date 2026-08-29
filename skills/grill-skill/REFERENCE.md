# Grill Skill Reference

## Caliper commands used by this skill

```bash
# Check spec is valid before running
caliper validate path/to/spec.eval.yaml

# First run — fast, catches spec errors
caliper run path/to/spec.eval.yaml --k 1

# Reliability run — after iterating on the skill
caliper run path/to/spec.eval.yaml --k 3

# Ablated run — before committing, proves the skill makes a difference.
# Run once and keep it: it cannot move when the skill's text changes.
caliper run path/to/spec.eval.yaml --k 3 --ablate my-skill
# Then diff it against the full run. A bare spec name resolves to that spec's
# LATEST run, so address the older side by its saved results path.
caliper compare .caliper/results/<spec>/<ablated-run>.json <spec>

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

`compare` also reports **skill drift** — a member of the neighbourhood whose
*text* changed between the two runs, read from the per-file hashes in each run's
snapshots. It is graded by provenance, not role: a drifted **git source** warns,
because the spec claimed where those bytes came from and the delta you are
reading is confounded; a drifted **path source** is shown without alarm, because
nothing was promised about a working file and that edit is usually the thing the
run exists to measure.

```
 ⚠ tdd changed between runs — git source, a1b2c3d → e4f5g6h; pin `ref:` to hold it fixed
   my-skill changed between runs — path, 4fc7951 → bcbcbde
```

This is a change in *text* at constant membership; a change in *membership* is
the separate neighbourhood warning.

## Inspecting failures

After any `caliper run`, failed tasks are shown automatically with their output
and `assert_evidence` — no extra command needed. Each attempt is tagged with an
`outcome`: a real `task_fail` reads as `✗`, while *unusable* attempts
(`infra_error` from a rate limit that outlasted its retries, `timeout`, or
`judge_error`)
read as `⊘` and are excluded from the score denominator, with a separate
"N unusable" count in the summary — so a throttled or judge-flaked run is not
mistaken for a skill regression. If `caliper run --fail-fast N` stopped a task
after repeated `infra_error` / `timeout` outcomes, the report marks it as
`ABORTED` and shows how many attempts ran. A run you stopped with Ctrl-C is
saved too, headed by an `interrupted:` line: its rates are computed over the
attempts that ran, so read them as a smaller sample rather than a worse skill —
and re-run before drawing a conclusion from a handful of attempts. `caliper list`
marks such a run with `⊘`, and `caliper compare` warns when either side is one,
so a shallow sample cannot quietly masquerade as a delta. A run that hit a
**spending cap** stops the same way, with the cap named as the cause — top up and
re-run rather than reading its numbers. A `throttled:` line under the usage
summary means attempts were retried before they landed: the scores are sound, but
the wall times were fought for. If a failure is still unclear, use
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
    - ".*\\.eval\\.yaml$"
    - "./.caliper/.*"

# Optional — only if the skill needs MCP tools. claude-code, hermes, codex backends.
mcp:
  weather:                       # local stdio server → mcp__weather__<tool>
    command: python3
    args: [./servers/weather.py]
    env:
      API_TOKEN: ${MCP_API_TOKEN}   # resolved from your shell at run time
  gdrive:                        # remote (hosted) server over HTTP/SSE
    type: http                   # http or sse
    url: https://mcp.example.com/gdrive
    headers:
      Authorization: Bearer ${GDRIVE_TOKEN}   # resolved from your shell at run time

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

  - name: Silence — <work no declared skill should answer>
    prompt: ...
    activates: []                # a trigger probe: no judge, no execution score
```

Each task needs at least one of `expect`, `assert` or `activates`.

## Triggering: does the description fire?

Skills are **installed** where the agent looks for them and never pasted into
the prompt, so whether the agent reaches for one is measurable. Two rules follow
for how you write prompts:

- **Never name the skill in a prompt.** "Use the commit-message skill to…"
  removes the very choice being measured. Write the prompt a real user would.
- **`activates:` asserts the exact set** of skills that loaded — `[a]` means `a`
  and nothing else, `[]` means silence. Names are the frontmatter `name:`, not
  filenames.

A task carrying only `activates:` is a **trigger probe**. It skips the judge
entirely, so it costs far less than an execution task, and reports as `trigger
only` rather than a zero. Two kinds are worth generating:

- **Neighbour probe** — declare a sibling skill in `skills:`, then give a prompt
  that belongs to *it* and assert `activates: [sibling]`. This catches a
  `description` that over-claims.
- **Silence probe** — unrelated work, `activates: []`.

Activation is scored separately from execution and never blended in, so a
near-zero score with a green activation column means the body is wrong, while a
red activation column means the `description` is.

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

If the skill under test needs MCP tools, declare them in a top-level `mcp:` block (a mapping keyed by server name) — a capability granted to the agent-under-test for the eval, part of the run environment like `sandbox:` (a sibling of it and of `skills:`), so they belong in the spec, not on the command line. A server is either **local stdio** (a `command`, optional `args`, optional `env`) or **remote** (`type: http`/`sse`, a `url`, optional `headers` for auth); the two field sets are mutually exclusive. Supported on **`claude-code`** (stdio + remote HTTP/SSE), **`hermes`** (stdio + remote header-auth; not remote OAuth), and **`codex`** (stdio + remote header-auth, translated into `[mcp_servers.*]` tables in the isolated `~/.codex/config.toml`; not remote OAuth). A tool call appears in the transcript as a namespaced name — `mcp__<server>__<tool>` on `claude-code` and `codex`, `mcp_<server>_<tool>` on `hermes` — so an `expect:` criterion can check the skill actually used it; word it around behaviour, not one backend's spelling, if the spec runs under more than one engine. Put secrets in a host env var and reference it as `${VAR}` inside a stdio `env:`, a remote `headers:`, or a remote `url:` — it resolves at the harness boundary from your shell at run time and never lands in the committed spec (an unset var fails the run). Running an `mcp:` spec on a backend that can't honor it is a hard error, not a silent no-op: `pi` has no MCP by design and will not honor `mcp:` natively — expose the capability as a CLI tool the skill drives or a pi extension, or run the eval on `claude-code`/`hermes`/`codex`.

## Backends

| Backend | Requires | Notes |
|---|---|---|
| `claude-code` | Claude Code CLI | Default for most skills |
| `codex` | Codex CLI | For Codex-targeted skills |
| `pi` | pi CLI (authenticated) | For pi / agentskills.io skills; native `--skill` loading |
| `hermes` | Hermes Agent CLI (authenticated) | Nous Research; normalized to a neutral agent, `hermes:<provider>/<model>` picks the model |

The skill engine (`--model`) and judge engine (`--judge-model`) are chosen independently at run time. Every backend is a CLI agent; for API billing, configure a CLI with an API key rather than selecting a separate backend. When `--judge-model` is omitted, the default `claude-code` judge pins `claude-sonnet-5` at execution time so it does not inherit a stale model from the installed Claude CLI; `RunMeta.judge_model` stays empty unless you pass `--judge-model` explicitly or the autorater reports what it used.

`hermes` is a stateful agent (persistent memory + persona), so Caliper strips it to a neutral agent per attempt — isolated `HERMES_HOME`, no `SOUL.md`/`MEMORY.md`, `--ignore-rules`, and only the spec's declared skills installed — and recovers the full trajectory via `hermes sessions export` after the `hermes -z` run.

## Results storage

Results are saved automatically to `.caliper/results/<spec-name>/<timestamp>.json`
alongside the spec file. Each attempt records its `outcome`, optional `usage`, and
optional `transcript` (ordered turns with `tool_name`/`tool_input`/`tool_output` when present)
so saved runs remain inspectable after the fact — including which MCP tools fired.
Older JSON without `transcript` still loads (`null`). `report` and `compare` do not
render the transcript; it is stored for later analysis.

## Troubleshooting

**`Judge model ... is unavailable` / `Judge authentication failed` / `Judge rate limited`**
The judge CLI reached the provider and the call was refused. Caliper classifies these at the harness boundary (from the CLI's structured output) and suggests passing `--judge-model <backend[:model]>` to pick an available judge engine or model.
