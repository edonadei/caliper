# Grill skill reference

`caliper <command> --help` lists every flag.

## Commands

```bash
caliper validate path/to/spec.eval.yaml                   # offline check: unknown keys, bad regexes, missing assert: files
caliper run path/to/spec.eval.yaml --k 1                  # first run: catches spec errors
caliper run path/to/spec.eval.yaml --k 3 --ablate my-skill   # the control, run once and kept
caliper run path/to/spec.eval.yaml --k 3                  # full run, after each skill edit
caliper run path/to/spec.eval.yaml --no-user-customizations  # isolated: only the spec's mcp: servers

# A bare spec name resolves to that spec's LATEST run, so address the control by
# its saved results path.
caliper compare .caliper/results/<spec>/<control-run>.json <spec>

caliper list <spec>                                       # its runs, ablated ones marked
caliper report <spec> --verbose                           # full output, passing tasks too
caliper report <spec> --run 2026-06-21T14-53-12Z --verbose
```

The engine is chosen at run time, never in the spec: `--model` for the skill
and `--judge-model` for the judge, each `backend`, `backend:model` or a bare
model (`--model codex`, `--model codex:gpt-5-codex`). Backends are
`claude-code` (default), `codex`, `pi`, and `hermes`.

## Spec skeleton

```yaml
skills:                          # installed at the agent's skills root, never
  - ./SKILL.md                   #   preloaded: the agent has to choose it
  - ../other-skill/SKILL.md      # a neighbour it could be confused with
  - repo: vercel-labs/agent-skills   # or a git source caliper clones
    ref: a1b2c3d                 # optional; omit to track the default branch
    path: skills/tdd/SKILL.md    # optional; defaults to SKILL.md at the root

sandbox:
  forbidden_files:               # extra patterns only: the spec itself and any
    - "./answers/.*"             #   .caliper/ directory are forbidden already

tasks:
  - name: Happy path — <what success looks like>
    setup: <optional shell command, run in the attempt workdir>
    prompt: <what a real user would type; never names the skill>
    expect: <natural-language success criterion>
    assert: |
      # optional deterministic check
    activates: [my-skill]

  - name: Edge case — <tricky but valid input>
    prompt: ...
    expect: ...
    activates: [my-skill]

  - name: Adversarial — <what the skill should refuse or avoid>
    prompt: ...
    expect: <describes the refusal or safe behavior>
    activates: [my-skill]

  - name: Neighbour — <a prompt that belongs to other-skill>
    prompt: ...
    activates: [other-skill]     # trigger probe: no judge, cheap

  - name: Silence — <work no declared skill should answer>
    prompt: ...
    activates: []                # trigger probe
```

Each task needs at least one of `expect`, `assert` or `activates`. A spec needs
at least one task, and task names must be unique: `compare` matches tasks across
runs by name. A spec that pins `backend`, `model` or a `judge:` block fails
validation.

## Writing tasks

- **Never name the skill in a prompt.** "Use the commit-message skill to…"
  removes the very choice being measured. Write the prompt a real user would.
- **`activates:` asserts the exact set** of skills that loaded: `[a]` means `a`
  and nothing else, `[]` means silence. Names are the frontmatter `name:`, not
  filenames. A skill the spec doesn't declare is never installed, so if yours
  delegates to another, declare it and list the whole chain.
- **Single-shot.** Each attempt is one prompt; nobody answers the agent's
  questions. For a skill that asks before acting, judge the first turn:
  `expect:` the question, `assert:` that nothing was done yet.
- **Attempt workdir.** `setup:`, the agent, `assert:` and `cleanup:` all run in
  one fresh, empty directory per attempt, so relative paths mean the same file
  to each. It is not the spec's directory and not a git repo: build what the task
  needs in `setup:` (`cp -R "$CALIPER_SPEC_DIR/fixture/." .`, `git init`).
  Fixed `/tmp` paths collide across attempts running in parallel. A failed
  `setup:` records an unusable `infra_error`.
- **`expect:` is a pass/fail criterion.** Say what evidence the judge should
  look for and what counts as failure:

  ```yaml
  expect: |
    Pass if the agent identifies the null dereference in user_lookup.py and
    explains the failing path. Fail if it only gives generic style advice,
    misses the bug, or claims tests passed without running them.
  ```

- **Add `assert:`** when the outcome is a fact an LLM judge might guess wrong:
  a file's existence or exact content, a command's exit code or output, git
  state, a JSON value, a test suite's result.
- **MCP tools.** If the skill needs MCP servers, declare them in a top-level
  `mcp:` block: stdio (`command`, `args`, `env`) or remote (`type: http`/`sse`,
  `url`, `headers`). Put secrets in host env vars as `${VAR}`. Stdio paths
  starting with `./` resolve from the spec's directory, and a server that can't
  start stops the run as a configuration error. `pi` has no MCP support;
  `claude-code`, `codex` and `hermes` do. By default a run also loads the user's
  own MCP servers and connectors, so a task may rely on one the user has; put
  `user_customizations: false` at the top of the spec for a portable score (see
  "Whose setup is measured" in [SKILL.md](SKILL.md)).

## Reading a run

Failed tasks are shown automatically with their output and `assert_evidence`.

- `✗` is a real `task_fail`. `⊘` is an unusable attempt (`infra_error`,
  `timeout`, `judge_error`), excluded from the score and counted separately. A
  run where every attempt was unusable measured nothing: it is saved, but
  `caliper run` exits `2`.
- Activation has its own column, never blended into the score. A task without
  `activates:` shows *skipped*.
- A run stopped with Ctrl-C, by `--fail-fast` (`ABORTED`), or by a spending cap
  is scored over the attempts it has: a smaller sample, not a worse skill.
  Re-run before drawing a conclusion.
- `compare` shows `Δ = b − a` per task; any drop is flagged. Token and wall-time
  deltas are shown too, and are never a regression. It warns when a git-sourced
  skill's text drifted between the runs (pin `ref:`), when the declared skills
  differ, or when the runs loaded user customizations differently.

## Naming convention

The spec lives next to the skill and shares its directory name:

```
skills/my-skill/SKILL.md
skills/my-skill/my-skill.eval.yaml   ← generated here
```

## Troubleshooting

**`Judge model ... is unavailable` / `Judge authentication failed` / `Judge rate limited`**
The judge CLI reached the provider and the call was refused. Pass
`--judge-model <backend[:model]>` to pick an available judge engine or model. An
unavailable judge model stops the run with exit `2`; an authentication failure or
a rate limit stays a per-attempt `judge_error`.
