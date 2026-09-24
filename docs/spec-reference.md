# Spec reference

A spec is a `.eval.yaml` file that says which skills to install and what
"working" means. This page is the full reference. For a first spec, start with
the [README quick start](../README.md#quick-start) or the
[Eval Starter Pack](../examples/starter-pack/).

## Full format

```yaml
skills:                         # installed where the agent looks for skills,
  - ./SKILL.md                  #   never pasted into the prompt
  - ../evaluate-skill/SKILL.md  # a path source: whatever that file says today
  - repo: vercel-labs/agent-skills   # a git source: Caliper clones it
    ref: a1b2c3d                     #   optional; omit to track the default branch
    path: skills/tdd/SKILL.md        #   optional; defaults to SKILL.md at the root
                                # omit `skills:` entirely for a bare agent

# Note: there is no `backend`/`model` or `judge:` block. The engine is picked
# at run time with `--model` / `--judge-model` (default: claude-code).

sandbox:
  extra_path:
    - ./bin                     # prepended to PATH inside each attempt
  forbidden_files:            # extra patterns; the spec itself and any
    - "./answers/.*"          #   .caliper/ directory are always forbidden

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
    setup: <shell command>      # optional; runs in the attempt workdir; failure skips the agent and judge
    cleanup: <shell command>    # optional; runs in the attempt workdir, even after setup failure
    prompt: <prompt sent to the agent>
    expect: <natural-language success condition>
    assert: |
      # optional inline Python assertion
      assert True

  - name: Task with external assertion script
    prompt: "Generate a report"
    assert: ./assertions/check_report.py

  - name: A neighbour's prompt, which yours must not hijack
    prompt: "How reliable is my commit-message skill? Run it 10 times."
    activates: [evaluate-skill]   # exactly these skills, and no others

  - name: Unrelated work, silence expected
    prompt: "Rename `resolved_model` to `engine_model` across the repo."
    activates: []                 # nothing should fire
```

Each task needs at least one of `expect`, `assert` or `activates`. Task IDs are
assigned automatically as `task-001`, `task-002`, and so on.

`caliper validate` (and `caliper run`, before its first attempt) also rejects an
unknown task or `sandbox:` key such as `asert:`, a `forbidden_files` entry that
isn't a valid regex, and an `assert:` script file that doesn't exist beside the
spec.

> **Upgrading an existing spec?** `skill:` became `skills:` in v0.10. See
> [MIGRATING-to-skills.md](MIGRATING-to-skills.md) for a short checklist,
> including the two traps a find-and-replace misses (stale `skill.path` inside
> `prompt:`/`expect:`/`assert:` strings, and prompts that name the skill they're
> testing).

## `skills:`, the neighbourhood

Every entry is installed at the agent's own skills root under its frontmatter
`name:`, and **nothing is preloaded**. Entries are peers: no entry is "the skill
under test", so `activates:` always names skills explicitly.

The set is closed. The agent sees these skills and nothing else, which is what
makes activation a measurement rather than a guess. It also means a skill you
*don't* declare can never activate. If yours delegates to another skill, declare
that one too and list the whole chain (`activates: [mine, helper]`), which makes
"did it actually delegate?" assertable.

A skill must be a `SKILL.md` in a directory, with frontmatter `name:` and
`description:`. A lone slash-command `.md` is rejected: with no name and no
description, there's nothing for an agent to discover.

### Path sources and git sources

An entry is written one of two ways, and the shape is the difference:

| Entry | Means |
|---|---|
| `- ./SKILL.md` | a **path source**: a file on your disk, whatever it says at run time |
| `- {repo: …, ref: …, path: …}` | a **git source**: Caliper clones it and resolves `ref:` to a commit |

Git sources let you give your `description` real competition without vendoring
someone else's repo into yours. One entry is one skill. Entries that share a repo
and commit share one clone, so naming five skills from a pack costs five entries
and one fetch.

`repo:` takes anything git can clone. A bare `owner/name` expands to
`https://github.com/owner/name`. A URL, an `scp`-style `git@host:owner/name`, or
a filesystem path is passed through untouched. To point at a *local* repo by
relative path, write `./owner/name`; the leading `./` is what tells it apart
from the shorthand.

`ref:` is optional. Without it, the entry tracks the default branch, so it
*will* move. That's allowed because Caliper records the commit it resolved and
`compare` tells you when it moved (see [Skill drift](#skill-drift)). Pinning is
still worth it: a pinned entry is fully offline once fetched, while an unpinned
one costs one `git ls-remote` per run.

`caliper run` fetches before the first attempt, so a bad `repo:` fails fast and
costs nothing. `caliper validate` never touches the network: it resolves git
sources from the cache when it can and reports the rest as *not cached*,
including when that means it couldn't check your `activates:` names.

Checkouts land in `~/.cache/caliper/skills/` (or `$XDG_CACHE_HOME/caliper/…`),
keyed by resolved commit. They're immutable, shared across every spec that names
them, and safe to delete. Set `CALIPER_CACHE_DIR` to put them elsewhere.

If a git source can't be fetched and isn't cached, the run **refuses**. A
missing member would measure your skill against competition that wasn't there.
If it's cached but the remote is unreachable, the run uses the cache and says
so.

A git source whose skill has a symlink pointing outside the cloned repo is
refused too: its bytes would come from the machine running the eval, not the
commit. A link to a shared file elsewhere in the repo is fine.

### Skill drift

`caliper compare` reports any member whose text changed between the two runs.
A **git source** that moved gets a warning: the spec said where its bytes came
from, so the delta you're reading is confounded. A **path source** that moved is
shown without alarm, since that's usually the edit the run exists to measure.

```
 ⚠ tdd changed between runs — git source, a1b2c3d → e4f5g6h; pin `ref:` to hold it fixed
   my-skill changed between runs — path, 4fc7951 → bcbcbde
```

This is a change in *text* with the same members. A change in *membership*
(different skills installed) raises a separate neighbourhood warning.

## `activates:`: did the agent reach for it?

`activates:` asserts the **exact set** of skills that loaded on each attempt.

| Form | Means |
|---|---|
| *(omitted)* | not asserted; the column still shows what loaded, dimmed |
| `activates: [a]` | exactly `a` fired, and nothing else |
| `activates: [a, b]` | both fired, which is how a delegating skill asserts its chain |
| `activates: []` | nothing fired; silence held |

A task with `activates:` and no `expect:`/`assert:` is a **trigger probe**. It
asks only what the agent reached for, skips the judge entirely (so it's much
cheaper than a graded task), and reports as `trigger only` rather than a zero.
Use it for neighbour and silence probes, where there's no work worth grading.

Activation is scored on its **own scoreboard**, never blended into the success
rate. A failing `description` and a failing body are fixed in different places,
so one number mixing them would point at neither.

## MCP servers (`mcp:`)

The optional `mcp:` block declares the [MCP](https://modelcontextprotocol.io)
servers the agent under test may use. It's part of the run environment, like
`sandbox:`, so it lives in the spec rather than behind a flag. It's a top-level
mapping keyed by server name, and it applies whether or not the spec declares
any skill.

Each server's tools appear in the transcript as a namespaced call that an
`expect:` judge can verify: `mcp__<server>__<tool>` on `claude-code` and
`codex`, `mcp_<server>_<tool>` on `hermes`. If the spec should run on more than
one engine, word `expect:` around the tool's behavior, not one backend's
spelling.

A server is either **local (stdio)**, a `command` the harness spawns, or
**remote (`type: http` or `sse`)**, a hosted endpoint at `url`. Remote is the
shape most connectors (Google Drive, Notion, and so on) use:

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

- **Transport is set by `type:`.** Omitted (or `stdio`) means a local
  `command`; `http`/`sse` means a remote `url`. The two field sets are mutually
  exclusive: a stdio server can't set `url`/`headers`, and a remote server can't
  set `command`/`args`/`env`.
- **Secrets stay out of the spec.** A value in a stdio `env:`, a remote
  `headers:`, or a remote `url:` may reference a host environment variable as
  `${VAR}`. It's resolved from your shell at run time, never written into the
  committed spec, and an unset variable fails the run with a clear message.
- **Server names** must match `[A-Za-z0-9_-]+` so the backend's namespaced tool
  handle is well-formed.
- **Local paths** in stdio `command` and `args` that start with `./` or `../`
  resolve from the spec's directory, so `args: [./servers/weather.py]` works
  wherever you run Caliper. Bare command names such as `python3` and bare
  arguments are passed through.
- **Servers are checked before the agent starts.** After each task's `setup:`,
  Caliper checks each declared stdio server in the attempt environment and
  checks that it answers an MCP initialization request. A missing, exiting, or
  unresponsive server stops the run with a configuration error rather than
  producing a task score. Servers removed by `--ablate` aren't checked.
- **Backend support varies.** See
  [MCP support by backend](backends.md#mcp-support-by-backend).

`caliper validate` checks the `mcp:` block and reports a malformed entry: a bad
name, an unknown key, an unknown `type`, a stdio server with a missing or blank
`command`, or a remote server missing `url`.

### Ablating a server

A declared server can be ablated for one run exactly like a skill:
`caliper run <spec> --ablate weather` leaves it out of the harness config, so the
agent never sees its tool definitions.

- If a skill and a server share a name, qualify it: `--ablate mcp:weather` for
  the server, `--ablate skill:weather` for the skill. An ambiguous bare name is
  refused rather than guessed at.
- The run records what it removed (`RunMeta.ablated`, as `mcp:weather`) and the
  servers it actually ran with (`RunMeta.mcp_servers`), so `caliper compare` can
  check the label against what the run had.
- An ablation that removes every server still isolates the attempt to zero
  servers instead of falling back to your ambient config. An authored
  `mcp: {}`, or no `mcp:` block at all, does the same.
- That includes your account's hosted connectors: `claude-code` always runs with
  `--strict-mcp-config`, and `codex` with its `apps` and `plugins` features
  turned off. The judge runs with the same switches, so it can't mistake its own
  connectors for the attempt's.
- `caliper run --inherit-mcp` gives a run your own servers and connectors back,
  merged with the declared ones. It's a flag, not a spec field: what it brings
  depends on the machine. `--ablate` still only names declared servers. See
  [Inheriting your own MCP setup](backends.md#inheriting-your-own-mcp-setup).

## Judging

### LLM autorater (`expect:`)

The judge engine reads the full attempt transcript and decides whether the
`expect` condition was met. Every backend captures tool-call traces, and the
judge sees them, so it can verify things like "the agent used tool X" without
relying on the final text alone.

The judge engine is chosen at run time and defaults to `claude-code`. Point it at
a different agent with `--judge-model` (for example `--judge-model codex`),
independently of the skill's `--model`.

### Deterministic assertions (`assert:`)

Python assertions run locally, in the [attempt workdir](#attempt-workdir). Use
them for facts the LLM judge might guess at:

- a file exists, or has exact contents
- JSON or schema validity
- command output
- images or screenshots
- repository state

```yaml
tasks:
  - name: Writes an output file
    prompt: "Write hello world to out.txt"
    assert: |
      from pathlib import Path
      path = Path("out.txt")
      assert path.exists(), "Output file was not created"
      assert path.read_text().strip() == "hello world"
```

When both `expect` and `assert` are present, both must pass.

## Attempt workdir

Every attempt gets a fresh, empty directory. `setup:`, the agent, `assert:`, the
judge and `cleanup:` all run in it, so a relative path means the same file to
each of them. It's deleted once the attempt is recorded.

It's not your spec's directory and not a git repository: a task that needs files
or a repo builds them in `setup:`. Hooks and assertions see two environment
variables:

| Variable | Value |
|---|---|
| `CALIPER_WORKDIR` | the attempt workdir |
| `CALIPER_SPEC_DIR` | the directory holding the `.eval.yaml` |

```yaml
tasks:
  - name: Fixes the failing test
    setup: cp -R "$CALIPER_SPEC_DIR/fixtures/broken-app/." .
    prompt: "The test suite fails. Fix it."
    assert: |
      import subprocess
      assert subprocess.run(["python", "-m", "pytest", "-q"]).returncode == 0
```

`assert: ./check.py` still resolves against the spec's directory; the script
runs in the workdir. See
[ADR 0026](adr/0026-an-attempt-runs-in-one-fresh-workdir.md).
