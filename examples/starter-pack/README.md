# Caliper Eval Starter Pack

Four copy-paste eval templates that take you from install to a real,
running eval in about five minutes. Each one targets a specific way agents
fail — and each one runs **green as-is** against a tiny bundled example, so you
can prove your setup works before you change a single line.

## Before you start

Install the CLI and make sure your agent backend is authenticated:

```bash
pipx install caliper-eval        # or: pip install caliper-eval
```

The templates default to the `claude-code` backend. If you use a different
agent, change the `backend:` line in any template (see
[Choosing a backend](../../README.md#choosing-a-backend)).

Two terms you'll see in every template:

- **`expect:`** is graded by an LLM judge that reads the run's transcript.
  **`assert:`** is plain Python that runs against the real side effect (a file,
  a log, command output). When both are present, both must pass.
- **`--k N`** runs each task `N` times; **pass@k** is the resulting reliability
  score — what fraction of runs succeeded, not whether one lucky run passed.

## The four templates

| # | Template | The failure it catches |
|---|----------|------------------------|
| 1 | [`01-false-success`](01-false-success/false-success.eval.yaml) | The agent says "Done!" but never did the work. |
| 2 | [`02-tool-misuse`](02-tool-misuse/tool-misuse.eval.yaml) | The agent calls the right tool with the **wrong arguments**. |
| 3 | [`03-runaway-loops`](03-runaway-loops/runaway-loops.eval.yaml) | The agent retries forever, burning time and tokens. |
| 4 | [`04-prompt-regression`](04-prompt-regression/prompt-regression.eval.yaml) | A prompt edit silently breaks cases that used to pass. |

### 1. False success — *grade the outcome, not the bragging*

An agent can confidently report success it never earned. If you only read its
final message, you'll believe it. This template grades the intended outcome
with `expect:` **and** checks the real side effect with `assert:` — the assert
reads the actual file, which is what catches the lie.

**Reach for it when** your skill is supposed to *produce* something — a file, a
commit, a database row, an API call.

### 2. Tool misuse — *check the arguments, not just that the tool fired*

"The agent called the tool" is not success. A misused tool fires with the wrong
arguments: deploys to prod instead of staging, alerts the wrong channel, emails
the wrong list. This template puts a tiny logging wrapper on `PATH` that records
every argument, then asserts on the arguments that matter.

**Reach for it when** your skill drives a tool where the *parameters* are the
whole point.

### 3. Runaway loops — *bound the step count*

An agent stuck on a failing step can loop forever. `--timeout` caps wall-clock
time as a backstop, but the sharper signal is **how many steps** it took. This
template gives the agent a tool that always fails plus a rule to stop after a
budget, and asserts that it didn't blow past the budget.

**Reach for it when** your skill has retries, polling, or any loop that could
run away.

### 4. Prompt regressions — *a fixed set you re-run on every change*

You fix one case and silently break three others. This template is a small,
fixed set of cases that already work, each with a deterministic `assert:`.
Re-run it after every prompt edit; when a case flips from green to red, your
last change caused a regression.

**Reach for it when** you're actively editing a prompt or `SKILL.md` and want a
tripwire.

## Run them

Each template runs from its own folder. From the repo:

```bash
cd examples/starter-pack/01-false-success
caliper run false-success.eval.yaml --k 3
```

You should see all tasks pass. That confirms Caliper is wired up correctly —
the CLI, your backend auth, and the judge all work end to end.

Do the same for the other three:

```bash
cd ../02-tool-misuse      && caliper run tool-misuse.eval.yaml --k 3
cd ../03-runaway-loops    && caliper run runaway-loops.eval.yaml --k 3 --timeout 90
cd ../04-prompt-regression && caliper run prompt-regression.eval.yaml --k 3
```

## Point a template at your own agent

Each template marks the lines you edit with a `👉 EDIT` comment — follow the
numbered markers and you'll catch every line that needs changing (watch for the
side-effect path, which can appear in `setup`, `cleanup`, `prompt`, and
`assert` at once). The edits boil down to:

1. **`skill.path`** — point it at your own `SKILL.md` (or delete it to test the
   bare agent with no skill).
2. **`skill.backend`** — your agent: `claude-code`, `codex`, `pi`, `claude-api`,
   or `openai-api`.
3. **`tasks[].prompt`** (and the matching `expect:`/`assert:`) — describe a real
   request and what a correct result looks like for *your* skill.

For templates 2 and 3, the fake tool under `bin/` is a stand-in so the example
runs without any external service. When you switch to your own agent, delete the
fake and point the `assert:` at your real side effect instead.

## What's a good next step?

- Add `--baseline` to prove the skill is doing the work, not the base agent:
  `caliper run false-success.eval.yaml --k 3 --baseline`.
- Commit your edited template next to your skill so contributors run the same eval.
- See the [main README](../../README.md) for the full spec format, judging, and
  CLI reference, or use `/grill-skill` to generate a spec interactively.
