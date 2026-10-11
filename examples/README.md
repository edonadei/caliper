# Examples

Five small evals, one per common way agents fail. Each folder holds a spec
(`*.eval.yaml`) and a tiny `SKILL.md` it tests, and each one passes as-is. Run
one first to confirm your setup works, then copy it next to your own skill and
edit the marked lines.

| Example | The failure it catches | Reach for it when your skill… |
| --- | --- | --- |
| [`01-false-success`](01-false-success/false-success.eval.yaml) | The agent says "Done!" but never did the work | produces something: a file, a commit, a row, an API call |
| [`02-tool-misuse`](02-tool-misuse/tool-misuse.eval.yaml) | The agent calls the right tool with the wrong arguments | drives a tool where the arguments are the point |
| [`03-runaway-loops`](03-runaway-loops/runaway-loops.eval.yaml) | The agent retries a failing step forever | retries, polls, or loops |
| [`04-prompt-regression`](04-prompt-regression/prompt-regression.eval.yaml) | A prompt edit breaks cases that used to pass | is being actively edited |
| [`05-context-boundary`](05-context-boundary/context-boundary.eval.yaml) | An old note is treated as the current source of truth | loads memory, transcripts, ADRs, or team notes |

## Run one

```bash
pipx install caliper-eval
cd examples/01-false-success
caliper run false-success.eval.yaml --k 3
```

Every task should pass 3/3. That proves the CLI, your agent's auth, and the
judge work end to end. The agent defaults to `claude-code`; pass `--model` to
use another (for example `--model codex:gpt-5-codex`, see
[Choosing an engine](../README.md#choosing-an-engine)).

The others run the same way from their own folders. Give `03-runaway-loops` a
`--timeout` so a looping agent can't run forever:

```bash
caliper run runaway-loops.eval.yaml --k 3 --timeout 90
```

## Two things to know before reading a spec

- **`expect:` vs `assert:`.** `expect:` is plain English, graded by an LLM
  judge that reads the transcript. `assert:` is Python run in the attempt's
  workdir against the real side effect: a file, a log, command output. When a
  task has both, both must pass.
- **`--k`** runs each task that many times. The score is the fraction of runs
  that passed, so one lucky run can't hide a flaky skill.

## What each example teaches

**False success: check the side effect, not the report.** The agent's final
message can claim work it never did. The `assert:` reads the file the agent was
asked to write, which is what catches the lie.

**Tool misuse: check the arguments, not that the tool ran.** A fake `notify`
CLI under `bin/` logs every argument it gets. The prompt asks for an alert but
doesn't say which channel. The skill says ops, and the `assert:` checks the
logged channel.

**Runaway loops: count the calls.** A fake `flaky-fetch` always fails and logs
each call. The skill sets a budget of three tries, and the `assert:` counts the
log. `--timeout` is only the backstop; the call count is the real signal.

**Prompt regression: a fixed set you re-run on every edit.** Four slugify cases
that already pass, each with an exact expected output. When one flips to red
after you change `SKILL.md`, that change caused it.

**Context boundary: prove which source was authoritative.** `setup:` writes an
ADR, an instructions file, and an old chat note that contradicts the ADR. The
skill writes a JSON receipt of what it loaded, what it trusted, and what it
dropped as stale, and the `assert:` checks the stale note didn't become the
decision. The receipt is one way to make this checkable; assert on whatever
your skill actually produces.

## Adapt one to your skill

Copy the folder next to your skill and follow the numbered `👉 EDIT` comments
in the spec. Usually that means:

1. Point `skills:` at your `SKILL.md`, or delete it to test the bare agent.
2. Rewrite each task's `prompt`, plus its `expect:` and `assert:`, for your
   skill. If you change an output path, change it in both the prompt and the
   assert.
3. In `02` and `03`, delete the fake tool under `bin/` and the `sandbox:` block
   that puts it on `PATH`, then point the `assert:` at your real tool's side
   effect.

Every attempt starts in a fresh, empty workdir shared by `setup:`, the agent,
and `assert:`. That's why the specs use relative paths like `note.txt`: a file
left by an earlier run can't fake a pass, and parallel attempts don't collide.
If a task needs starting files, create them in `setup:`.

## Next: check the skill earns its place

Run the same tasks without the skill and diff the two runs. If the scores
match, the base agent was doing the work.

```bash
caliper run false-success.eval.yaml --k 3 --ablate save-note
caliper run false-success.eval.yaml --k 3
caliper compare .caliper/results/false-success/<ablated-run>.json false-success
```

`false-success` on the right means that spec's latest run. `caliper list`
shows run IDs. The ablated run depends only on the tasks, so keep it and
re-diff as the skill changes.

For the full spec format see [docs/spec-reference.md](../docs/spec-reference.md),
or run `/grill-skill` to have an agent write a spec with you.
